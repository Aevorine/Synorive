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
    fun download(url: String, targetDir: File, fileName: String): Flow<DownloadProgress> = flow {
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
                    if (!partFile.renameTo(finalFile)) {
                        throw IOException("下载完成但改名失败，磁盘可能已满")
                    }
                    emit(DownloadProgress(transferred, if (total > 0) total else transferred))
                }
            }
        }
    }.flowOn(Dispatchers.IO)
}
