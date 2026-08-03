package com.synorive.mobile.data.network

import android.content.Context
import android.net.Uri
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody

/**
 * 分享进来的图片/视频、拍照的照片，App 拿到的都是 `content://` URI，不是能直接
 * 传给 OkHttp 的文件。先落一份到缓存目录，再包成 multipart part——先落盘而不是
 * 直接读流传输，是为了让大文件（视频）走磁盘而不是整个读进内存，避免 OOM。
 */
object UploadUtils {

    suspend fun copyToCache(context: Context, uri: Uri): File = withContext(Dispatchers.IO) {
        val resolver = context.contentResolver
        val dir = File(context.cacheDir, "uploads").apply { mkdirs() }
        val guessedExt = resolver.getType(uri)?.substringAfterLast('/')?.takeIf { it.length in 1..8 }
        val name = "u${System.currentTimeMillis()}${if (guessedExt != null) ".$guessedExt" else ""}"
        val dest = File(dir, name)
        resolver.openInputStream(uri)?.use { input ->
            dest.outputStream().use { output -> input.copyTo(output) }
        } ?: error("打不开这个文件：$uri")
        dest
    }

    fun toMultipart(context: Context, file: File, uri: Uri): MultipartBody.Part {
        val mediaType = (context.contentResolver.getType(uri) ?: "application/octet-stream").toMediaTypeOrNull()
        val body = file.asRequestBody(mediaType)
        return MultipartBody.Part.createFormData("file", file.name, body)
    }

    /** 用完清掉——这些只是传输用的临时副本，长期缓存交给 Room 那份轻量索引，不是这里。 */
    fun cleanupCache(context: Context) {
        File(context.cacheDir, "uploads").listFiles()?.forEach { it.delete() }
    }
}
