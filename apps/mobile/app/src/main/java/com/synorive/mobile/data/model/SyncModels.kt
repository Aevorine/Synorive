package com.synorive.mobile.data.model

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * E17/6.5 同步的线上模型。
 *
 * 🔴 **字段名必须和 `engine/synorive/api/routes.py` 的 pydantic 模型逐字对上。**
 * 对不上的后果是反序列化成默认值（空串 / 0 / 空表），请求照发、HTTP 200 照回，
 * 而实际带过去的是空数据 —— 这类跨语言字段名不一致是这个项目里
 * 最常见的一种"运行正常但功能无效"。
 *
 * 🔴 **必须标 `@Serializable`。** 这个工程的 Retrofit 用的是
 * kotlinx.serialization（不是 Gson，见 `NetworkModule.kt`）。
 * 漏了这个注解不会编译报错，而是**运行时**抛
 * `SerializationException: Serializer for class ... is not found` ——
 * 整个接口直接不可用，而错误信息完全不指向"少了个注解"。
 */

@Serializable
data class SyncPairRequest(
    val passphrase: String,
    /** 第一次配对不传，由引擎生成并返回；第二台设备把它原样带回来 */
    val salt: String? = null,
)

@Serializable
data class SyncPairResponse(
    val salt: String = "",
    val fingerprint: String = "",
    val deviceId: String = "",
    val challenge: SyncChallenge? = null,
    val note: String = "",
)

@Serializable
data class SyncChallenge(
    val nonce: String = "",
    val mac: String = "",
)

@Serializable
data class SyncStatusResponse(
    val deviceId: String = "",
    val lamport: Long = 0,
    val queued: Int = 0,
    val pending: Int = 0,
    val entities: Int = 0,
    val tombstones: Int = 0,
    /** 🔴 false = **同步整个不可用**，不是"降级成明文同步" */
    val cryptoAvailable: Boolean = false,
    val note: String = "",
)

@Serializable
data class SyncPullRequest(val limit: Int = 200)

/**
 * 加密信封。
 *
 * 🔴 用 `JsonElement` 而不是 `Map<String, Any>`：kotlinx.serialization
 * **不支持 `Any`**，写成 `Map<String, Any>` 会在运行时抛
 * "Serializer for class 'Any' is not found"。而信封里的 `v` 是数字、
 * 其余是字符串，类型不齐，也不能简单写成 `Map<String, String>`。
 */
@Serializable
data class SyncEnvelope(
    val v: Int = 0,
    val nonce: String = "",
    val ct: String = "",
    val aad: String = "",
)

@Serializable
data class SyncPullResponse(
    val envelope: SyncEnvelope = SyncEnvelope(),
    val opIds: List<String> = emptyList(),
    val count: Int = 0,
    val deviceId: String = "",
    val note: String = "",
)

@Serializable
data class SyncPushRequest(val envelope: SyncEnvelope)

@Serializable
data class SyncMergeResponse(
    val applied: Int = 0,
    val skipped: Int = 0,
    /**
     * 每条被跳过的原因。
     * 🔴 这个字段**必须显示给用户**：只报"应用 3 条、跳过 7 条"的话，
     * 用户看到同步完成而他刚写的东西没出现，无从判断是丢了还是被覆盖了。
     */
    val skippedDetail: List<Map<String, String>> = emptyList(),
    val lamport: Long = 0,
    val note: String = "",
)

@Serializable
data class SyncAckRequest(val ids: List<String>)

@Serializable
data class SyncAckResponse(val marked: Int = 0)

/**
 * 一条同步操作的线上形状，和 Python `queue.Op.to_dict()` 对齐。
 *
 * 🔴 `payload` 用 `JsonElement`：它的内容随 entity 变（笔记正文 / 标签数组 / …），
 * 定成具体类型会在加新 entity 时静默丢字段。
 */
@Serializable
data class SyncOpWire(
    val id: String = "",
    val entity: String = "item",
    val entityId: String = "",
    val kind: String = "upsert",
    val payload: JsonElement? = null,
    val device: String = "",
    val lamport: Long = 0,
    val wallTs: Double = 0.0,
)
