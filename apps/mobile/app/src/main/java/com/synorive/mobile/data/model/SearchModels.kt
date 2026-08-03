package com.synorive.mobile.data.model

import kotlinx.serialization.Serializable

/** 对应引擎 `POST /api/search` 的请求体（engine/synorive/api/routes.py 的 SearchRequest）。 */
@Serializable
data class SearchRequest(
    val query: String = "",
    val limit: Int = 30,
    val offset: Int = 0,
    /**
     * "keyword" = 只跑关键词/FTS 那两路，几十毫秒出结果，适合手机端边打字边出结果；
     * "semantic" = 连向量检索一起跑，更准但慢一档。手机端首屏用 keyword，
     * 用户点「更准的结果」再补一次 semantic —— 跟桌面端瀑布式加载是同一个思路。
     */
    val stage: String = "keyword",
    val rerank: Boolean = false,
    val explain: Boolean = false,
    val answer: Boolean = false,
)

@Serializable
data class SearchResponse(
    val queryId: String = "",
    val stage: String = "",
    val final: Boolean = true,
    val hits: List<SearchHit> = emptyList(),
    val totalEstimate: Int = 0,
    val elapsedMs: Double = 0.0,
)
