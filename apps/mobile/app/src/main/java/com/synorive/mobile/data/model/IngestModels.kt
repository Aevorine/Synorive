package com.synorive.mobile.data.model

import kotlinx.serialization.Serializable

/** 对应引擎 `POST /api/ingest` 的请求体。source 固定传 "mobile"——
 *  shared-types 的 SourceKind 里专门留了这一档，界面上就能区分
 *  "这条是手机分享/拍照投进来的"，跟桌面端监听目录、剪贴板哨兵分开统计。 */
@Serializable
data class IngestRequest(
    val targets: List<String>,
    val source: String = "mobile",
    val recursive: Boolean = false,
    val priority: String = "normal",
    val tags: List<String>? = null,
    val allowCloud: Boolean = false,
)

@Serializable
data class IngestJob(
    val jobId: String = "",
    val status: String = "running",
    val totalItems: Int = 0,
)

/** `/api/upload` 的返回：文件已经落到引擎那台机器磁盘上的路径，
 *  接下来拿它去喂 `/api/ingest` 或 `/api/search/by-image`。 */
@Serializable
data class UploadResponse(
    val path: String,
    val sizeBytes: Long = 0,
    val filename: String? = null,
)
