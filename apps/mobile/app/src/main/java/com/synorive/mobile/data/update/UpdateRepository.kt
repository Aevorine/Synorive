package com.synorive.mobile.data.update

import com.synorive.mobile.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * U 组 · 查更新 + 下载 APK
 * ============================================================
 * 🔴 **这里必须用一个独立的 OkHttpClient，不能复用 NetworkModule 那个。**
 *    那个客户端上挂着 `DynamicBaseUrlInterceptor`，它会把**每一个**请求的
 *    host:port 改写成当前配对的那台电脑。拿它去请求 api.github.com，
 *    请求会被悄悄发到局域网里的引擎上，引擎回 404，
 *    界面显示"查不到更新" —— 而真正的原因和 GitHub、和网络都没关系。
 *    这类错误没有任何异常栈能指向根因，只能靠这条注释拦住。
 *
 * 🔴 **不带任何认证。** 公开仓的 releases/latest 是匿名可读的
 *    （限速 60 次/小时/IP，对"偶尔查一次更新"绰绰有余）。
 *    绝不在客户端里放 token —— APK 是可以解包的，放进去等于公开。
 */
class UpdateRepository(
    private val repo: String = BuildConfig.UPDATE_REPO,
    private val currentVersionCode: Long = versionCodeFromTag(BuildConfig.VERSION_NAME) ?: 0L,
) {
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        // APK 有几十 MB，慢速网络下别提前掐断
        .callTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    val currentVersionName: String get() = BuildConfig.VERSION_NAME
    val releasesPageUrl: String get() = "https://github.com/$repo/releases/latest"

    suspend fun check(): UpdateCheck = withContext(Dispatchers.IO) {
        val url = "https://api.github.com/repos/$repo/releases/latest"
        val request = Request.Builder()
            .url(url)
            .header("Accept", "application/vnd.github+json")
            .header("User-Agent", "Synorive-Android")
            .build()

        try {
            client.newCall(request).execute().use { resp ->
                if (resp.code == 404) {
                    return@withContext UpdateCheck.Failed(
                        "查不到发布信息（404）。可能仓库还是私有的，或者还没发过正式版。"
                    )
                }
                if (resp.code == 403) {
                    return@withContext UpdateCheck.Failed(
                        "GitHub 暂时限流了（403），过一会儿再试。"
                    )
                }
                if (!resp.isSuccessful) {
                    return@withContext UpdateCheck.Failed("GitHub 返回 HTTP ${resp.code}")
                }
                val body = resp.body?.string().orEmpty()
                if (body.isBlank()) return@withContext UpdateCheck.Failed("GitHub 返回了空内容")

                val release = json.decodeFromString(GhRelease.serializer(), body)
                if (release.draft || release.prerelease) return@withContext UpdateCheck.UpToDate

                val remoteCode = versionCodeFromTag(release.tagName)
                    ?: return@withContext UpdateCheck.Failed(
                        "看不懂发布的版本号「${release.tagName}」，规范应该是 v0.1.1 这种形式。"
                    )
                if (remoteCode <= currentVersionCode) return@withContext UpdateCheck.UpToDate

                val apk = release.assets.firstOrNull { it.name.endsWith(".apk", ignoreCase = true) }
                    ?: return@withContext UpdateCheck.NoApkAsset(
                        versionName = release.tagName.removePrefix("v"),
                        releaseUrl = release.htmlUrl.ifBlank { releasesPageUrl },
                    )

                UpdateCheck.Available(
                    versionName = release.tagName.removePrefix("v"),
                    versionCode = remoteCode,
                    notes = release.body.trim(),
                    releaseUrl = release.htmlUrl.ifBlank { releasesPageUrl },
                    apkUrl = apk.downloadUrl,
                    apkSize = apk.size,
                )
            }
        } catch (e: IOException) {
            UpdateCheck.Failed("连不上 GitHub：${e.message ?: "网络不可达"}")
        } catch (e: Exception) {
            UpdateCheck.Failed("解析发布信息失败：${e.message ?: e::class.simpleName}")
        }
    }

    /**
     * 下载 APK 到缓存目录，边下边发进度，最后一个事件的 transferred == total。
     *
     * 目标文件**先写 .part 再改名**：中途断网留下的半截文件如果直接叫 .apk，
     * 下次进来会被当成"已经下好了"直接拿去装，系统报「解析程序包时出现问题」，
     * 而用户完全看不出是上次没下完。
     */
    fun download(
        url: String,
        targetDir: File,
        fileName: String,
        /** GitHub 报的资产字节数。0 = 不知道，跳过长度校验 */
        expectedSize: Long = 0L,
    ): Flow<DownloadProgress> = flow {
        // 🔴 只下 GitHub 的东西。asset URL 本来就来自 HTTPS 的 GitHub API，
        //    正常情况下不可能指向别处 —— 但这条链路的终点是**把文件交给系统安装器**，
        //    是全 App 后果最重的一步，值得为"万一"再加一道最便宜的闸。
        //    （真正兜底的是安卓的签名校验：签名对不上的包根本装不上去。
        //     这里挡的是"下载阶段就别去连奇怪的主机"。）
        requireGitHubHost(url)

        if (!targetDir.exists()) targetDir.mkdirs()
        val finalFile = File(targetDir, fileName)
        val partFile = File(targetDir, "$fileName.part")
        if (finalFile.exists()) finalFile.delete()
        if (partFile.exists()) partFile.delete()

        val request = Request.Builder()
            .url(url)
            .header("User-Agent", "Synorive-Android")
            .build()

        client.newCall(request).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("下载失败：HTTP ${resp.code}")
            val body = resp.body ?: throw IOException("下载失败：响应没有内容")
            val total = body.contentLength()

            body.byteStream().use { input ->
                partFile.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var transferred = 0L
                    var lastEmitted = 0L
                    while (true) {
                        val read = input.read(buffer)
                        if (read == -1) break
                        output.write(buffer, 0, read)
                        transferred += read
                        // 每 256KB 发一次，不是每个 buffer 发一次 ——
                        // 后者在快网络下一秒能发几百个事件，Compose 重组会把界面拖卡
                        if (transferred - lastEmitted >= 256 * 1024) {
                            lastEmitted = transferred
                            emit(DownloadProgress(transferred, total))
                        }
                    }
                    output.flush()

                    // 🔴 **循环正常结束 ≠ 下完了。** 服务端提前关连接时
                    //    read() 一样返回 -1，不抛异常 —— 拿到的是个截断的 APK，
                    //    改完名就是一个"看起来下好了"的坏包，系统安装器只会说
                    //    「解析程序包时出现问题」，用户完全联想不到是没下完。
                    //    这就是那条"看字节数不看退出码"的坑在这条链路上的样子。
                    val want = if (expectedSize > 0) expectedSize else total
                    if (want > 0 && transferred != want) {
                        partFile.delete()
                        throw IOException(
                            "下载不完整：只拿到 ${transferred / 1024} KB，应该是 ${want / 1024} KB。" +
                                "网络中断了，重试一次。"
                        )
                    }

                    if (!partFile.renameTo(finalFile)) {
                        throw IOException("下载完成但改名失败，磁盘可能已满")
                    }
                    emit(DownloadProgress(transferred, if (want > 0) want else transferred))
                }
            }
        }
    }.flowOn(Dispatchers.IO)

    /**
     * 下载地址必须是 GitHub 的，且必须是 https。
     *
     * GitHub 的 release 资产会 302 到 `objects.githubusercontent.com`，
     * OkHttp 自动跟随重定向 —— 所以两个域都要放行。
     */
    private fun requireGitHubHost(url: String) {
        val u = runCatching { java.net.URI(url) }.getOrNull()
            ?: throw IOException("下载地址解析不了：$url")
        if (!"https".equals(u.scheme, ignoreCase = true)) {
            throw IOException("拒绝从非 HTTPS 地址下载安装包：$url")
        }
        val host = u.host?.lowercase().orEmpty()
        val ok = host == "github.com" ||
            host.endsWith(".github.com") ||
            host.endsWith(".githubusercontent.com")
        if (!ok) {
            throw IOException("拒绝从非 GitHub 主机下载安装包：$host")
        }
    }
}
