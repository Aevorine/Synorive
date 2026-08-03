package com.synorive.mobile.data.model

import kotlinx.serialization.Serializable

/** 对应 `POST /api/web/reverse-image`（W5）的请求体：itemId 和 path 二选一。 */
@Serializable
data class ReverseImageApiRequest(
    val itemId: String? = null,
    val path: String? = null,
    val limit: Int = 20,
)

@Serializable
data class ReverseImageHit(
    val title: String = "",
    val pageUrl: String = "",
    val thumbnailUrl: String = "",
    val imageUrl: String = "",
    val kind: String = "",
)

@Serializable
data class ReverseImageResult(
    val pagesIncluding: List<ReverseImageHit> = emptyList(),
    val visualSimilar: List<ReverseImageHit> = emptyList(),
    val bestGuess: String? = null,
    val error: String? = null,
)

/** 对应 `POST /api/web/reverse-video`（W6）——只接受库里已有、且已经跑完场景检测的条目。 */
@Serializable
data class ReverseVideoApiRequest(
    val itemId: String,
    val maxFrames: Int = 5,
)

@Serializable
data class VideoSourceCandidate(
    val pageUrl: String = "",
    val title: String = "",
    val matchedKeyframes: Int = 0,
    val thumbnailUrl: String = "",
)

@Serializable
data class ReverseVideoResult(
    val candidates: List<VideoSourceCandidate> = emptyList(),
    val framesTried: Int = 0,
    val error: String? = null,
)
