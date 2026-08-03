package com.synorive.mobile.data.model

import kotlinx.serialization.Serializable

/** 对应引擎 `GET /health`。手机端只用它做两件事：配对时"这地址真的是 Synorive 吗"的探活，
 *  和状态栏里显示"库里有多少条内容"。 */
@Serializable
data class HealthResponse(
    val ok: Boolean = false,
    val version: String = "",
    val indexedItems: Int = 0,
)
