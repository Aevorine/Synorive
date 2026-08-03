package com.synorive.mobile.data.update

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * U 组 · 安卓端自更新的数据模型
 * ============================================================
 * 更新源是 GitHub Releases 的公开 API：
 *   GET https://api.github.com/repos/<owner>/<repo>/releases/latest
 * 公开仓不需要任何 token —— 这正是「先转公开再做自更新」这个顺序的原因。
 * 私有仓的话应用里得内嵌一个 PAT，等于把它发给每个装了 App 的人。
 */

@Serializable
data class GhRelease(
    /** 形如 v0.1.1。**版本号的真相在 tag 上**，name 字段用户可以随便写 */
    @SerialName("tag_name") val tagName: String = "",
    val name: String = "",
    val body: String = "",
    val draft: Boolean = false,
    val prerelease: Boolean = false,
    @SerialName("html_url") val htmlUrl: String = "",
    val assets: List<GhAsset> = emptyList(),
)

@Serializable
data class GhAsset(
    val name: String = "",
    val size: Long = 0,
    @SerialName("browser_download_url") val downloadUrl: String = "",
)

/**
 * 查更新的结果。
 *
 * `Unavailable` 和 `Failed` 必须分开：前者是"查到了，你已是最新"，
 * 后者是"没查成"。合成一个的话，网络不通会显示成"已是最新"——
 * 用户永远收不到更新，而且完全没有异常现象可以让他察觉。
 */
sealed interface UpdateCheck {
    data object UpToDate : UpdateCheck
    data class Available(
        val versionName: String,
        val versionCode: Long,
        val notes: String,
        val releaseUrl: String,
        val apkUrl: String,
        val apkSize: Long,
    ) : UpdateCheck

    /**
     * 查到了新版本，但那个 Release 里**没有 APK 附件**。
     * 这是发布时最容易犯的错（只传了 exe 忘了传 apk），
     * 而它在用户那边的表现如果不单列一档，就和"已是最新"一模一样。
     */
    data class NoApkAsset(val versionName: String, val releaseUrl: String) : UpdateCheck

    data class Failed(val message: String) : UpdateCheck
}

/** 下载进度。total 为 -1 表示服务端没给 Content-Length，只能显示已下多少 */
data class DownloadProgress(val transferred: Long, val total: Long) {
    val percent: Int
        get() = if (total > 0) ((transferred * 100) / total).toInt().coerceIn(0, 100) else -1
}

/**
 * 从 tag 反推 versionCode。
 *
 * 约定：`v<major>.<minor>.<patch>` → major*10000 + minor*100 + patch。
 * 0.1.1 → 101，0.2.0 → 200，1.0.0 → 10000。这和 build.gradle.kts 里
 * 手写的 versionCode 是**两套编号**，所以比较用的是这个推导值，
 * 不是 BuildConfig.VERSION_CODE。
 *
 * 🔴 别改成"比较 versionName 字符串大小" —— "0.1.10" < "0.1.9" 在字典序下成立，
 *    到了第 10 个补丁版就会静默地停止提示更新。
 */
fun versionCodeFromTag(tag: String): Long? {
    val cleaned = tag.trim().removePrefix("v").removePrefix("V")
    val parts = cleaned.split('.', limit = 4)
    if (parts.size < 2) return null
    val nums = parts.take(3).map { part ->
        // 允许 0.1.1-beta2 这种后缀，取前导数字
        val digits = part.takeWhile { it.isDigit() }
        digits.toLongOrNull() ?: return null
    }
    val major = nums.getOrElse(0) { 0 }
    val minor = nums.getOrElse(1) { 0 }
    val patch = nums.getOrElse(2) { 0 }
    return major * 10000 + minor * 100 + patch
}
