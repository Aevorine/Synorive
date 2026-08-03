package com.synorive.mobile.data.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import java.io.File

/**
 * U 组 · 把下载好的 APK 交给系统安装器
 * ============================================================
 * 应用**自己装不了应用** —— 能做的只有把 APK 递给系统，剩下的是
 * 系统弹框、用户点确认。这条链路上有三个各自独立的失败点，
 * 每一个的报错都不指向真正的原因：
 *
 *  ① 给了 file:// URI → API 24 起直接 FileUriExposedException 崩溃
 *  ② FileProvider 的 authority 或 path 对不上 → IllegalArgumentException
 *     "Failed to find configured root"，看不出是 res/xml/file_paths.xml 少了一行
 *  ③ 没有 REQUEST_INSTALL_PACKAGES 权限、或用户没给"允许安装未知应用"
 *     → API 26+ 上 startActivity 被系统静默拦掉，什么都不发生
 *
 * ③ 是最坑的一个，因为它**没有异常**。所以 [canRequestInstall] 要在
 * 点安装之前先查，查不过就把用户送去那个设置页，而不是让他点了没反应。
 */
object ApkInstaller {

    /** 下载目录。必须和 res/xml/file_paths.xml 里的 `path="updates/"` 一致 */
    fun downloadDir(context: Context): File = File(context.cacheDir, "updates")

    /** API 26 起「允许安装未知应用」是按应用授权的，没有全局开关了 */
    fun canRequestInstall(context: Context): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.packageManager.canRequestPackageInstalls()
        } else {
            true
        }

    /**
     * 打开「允许安装未知应用」的系统设置页，并且**直接定位到本应用**。
     * 不带 package 的话会落在一个长长的应用列表里，用户得自己翻。
     */
    fun openInstallPermissionSettings(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val intent = Intent(
            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:${context.packageName}"),
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(intent) }
    }

    /**
     * 唤起系统安装器。
     * @return null 表示已经拉起来了；非 null 是给用户看的失败原因
     */
    fun install(context: Context, apk: File): String? {
        if (!apk.exists()) return "安装包不见了，请重新下载。"
        // 🔴 只看"文件存在"不够。下载中断留下的 0 字节文件同样存在，
        //    交给系统安装器会得到「解析程序包时出现问题」这种毫无指向性的报错
        if (apk.length() <= 0L) return "安装包是空的（可能上次没下完），请重新下载。"
        if (!canRequestInstall(context)) {
            return "系统还没允许 Synorive 安装应用。点下面的按钮去打开这个开关，再回来安装。"
        }

        return try {
            val uri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                apk,
            )
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                // 这两个 flag 缺一不可：没有读权限授予，安装器打不开这个 content://；
                // 没有 NEW_TASK，从非 Activity 上下文启动会抛异常
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
            null
        } catch (e: Exception) {
            "拉起安装器失败：${e.message ?: e::class.simpleName}"
        }
    }

    /** 清掉残留的旧安装包和半截文件。装完之后没必要留着几十 MB */
    fun cleanup(context: Context) {
        runCatching {
            downloadDir(context).listFiles()?.forEach { it.delete() }
        }
    }
}
