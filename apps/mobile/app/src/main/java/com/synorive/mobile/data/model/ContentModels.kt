package com.synorive.mobile.data.model

import kotlinx.serialization.Serializable

/**
 * 字段名要跟 packages/shared-types/src/index.ts 的 ContentItem 对齐——
 * 那份文件是整条链路（引擎/桌面端/安卓端/MCP）共用的唯一真相源。
 * 这里只挑手机端界面真正用得到的字段，其余的（meta 里那些模态专属信息等）
 * 不需要就不建模，多一个字段就多一处可能跟服务端悄悄脱节的地方。
 */
@Serializable
data class ContentItem(
    val id: String,
    val modality: String = "text",
    val source: String = "file",
    val status: String = "ready",
    val title: String = "",
    val locator: String = "",
    val snippet: String? = null,
    val mime: String? = null,
    val sizeBytes: Long? = null,
    val createdAt: String = "",
    val updatedAt: String = "",
    val tags: List<String> = emptyList(),
    val thumbPath: String? = null,
)

@Serializable
data class HitLocation(
    val chunkIndex: Int? = null,
    val page: Int? = null,
    val startSec: Double? = null,
    val endSec: Double? = null,
    val section: String? = null,
)

@Serializable
data class SearchHit(
    val item: ContentItem,
    val score: Double = 0.0,
    val highlight: String? = null,
    val location: HitLocation? = null,
)
