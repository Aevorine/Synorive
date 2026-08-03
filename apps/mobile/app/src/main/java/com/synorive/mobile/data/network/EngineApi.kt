package com.synorive.mobile.data.network

import com.synorive.mobile.data.model.HealthResponse
import com.synorive.mobile.data.model.IngestJob
import com.synorive.mobile.data.model.IngestRequest
import com.synorive.mobile.data.model.ReverseImageApiRequest
import com.synorive.mobile.data.model.ReverseImageResult
import com.synorive.mobile.data.model.ReverseVideoApiRequest
import com.synorive.mobile.data.model.ReverseVideoResult
import com.synorive.mobile.data.model.SearchRequest
import com.synorive.mobile.data.model.SearchResponse
import com.synorive.mobile.data.model.SyncAckRequest
import com.synorive.mobile.data.model.SyncAckResponse
import com.synorive.mobile.data.model.SyncMergeResponse
import com.synorive.mobile.data.model.SyncPairRequest
import com.synorive.mobile.data.model.SyncPairResponse
import com.synorive.mobile.data.model.SyncPullRequest
import com.synorive.mobile.data.model.SyncPullResponse
import com.synorive.mobile.data.model.SyncPushRequest
import com.synorive.mobile.data.model.SyncStatusResponse
import com.synorive.mobile.data.model.UploadResponse
import okhttp3.MultipartBody
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part

/**
 * 桌面引擎既有的 HTTP API（engine/synorive/api/routes.py），手机端和桌面端、
 * MCP、CLI 共用同一套——路径和字段名必须跟 Python 的 pydantic 模型一致，
 * 改了那边要同步改这里。
 */
interface EngineApi {
    /** 不在 /api 前缀下，且不需要配对令牌——配对流程靠它探活 */
    @GET("health")
    suspend fun health(): HealthResponse

    @POST("api/search")
    suspend fun search(@Body req: SearchRequest): SearchResponse

    @POST("api/ingest")
    suspend fun ingest(@Body req: IngestRequest): IngestJob

    /** A16 专用：手机上的文件对引擎所在机器来说没有"本机路径"，先传上来落盘 */
    @Multipart
    @POST("api/upload")
    suspend fun upload(@Part file: MultipartBody.Part): UploadResponse

    /** W5：这张图在网上还出现在哪些地方 */
    @POST("api/web/reverse-image")
    suspend fun reverseImage(@Body req: ReverseImageApiRequest): ReverseImageResult

    /** W6：这段视频的出处——只认库里已经跑完场景检测的条目，itemId 必填 */
    @POST("api/web/reverse-video")
    suspend fun reverseVideo(@Body req: ReverseVideoApiRequest): ReverseVideoResult

    // ── E17 端到端加密同步 ｜ 6.5 离线队列 ──────────────────
    //
    // 路径和字段名必须和 engine/synorive/api/routes.py 的 pydantic 模型一致。
    //
    // 🔴 **push/pull 都不带口令。** 口令在 /sync/pair 时派生成密钥留在
    // 引擎内存里，之后收发都用那把钥匙。每个请求都带口令的话，
    // 口令会在网络上来回传 —— 端到端加密最不该做的就是这个。

    /** 配对：口令 + 盐 → 密钥指纹。**指纹要拿去和另一台设备肉眼比对** */
    @POST("api/sync/pair")
    suspend fun syncPair(@Body req: SyncPairRequest): SyncPairResponse

    @GET("api/sync/status")
    suspend fun syncStatus(): SyncStatusResponse

    /** 把桌面端待推的操作拉过来（一个加密信封） */
    @POST("api/sync/pull")
    suspend fun syncPull(@Body req: SyncPullRequest): SyncPullResponse

    /** 把手机这边的操作推过去。整批是**一个信封**，不是明文数组 */
    @POST("api/sync/push")
    suspend fun syncPush(@Body req: SyncPushRequest): SyncMergeResponse

    /**
     * 确认收到。
     * 🔴 **拉完必须调它**，否则桌面端那批 `sent` 永远是 0，
     * 每次同步都会把同样的操作重推一遍 —— 不报错，只是越来越慢
     */
    @POST("api/sync/ack")
    suspend fun syncAck(@Body req: SyncAckRequest): SyncAckResponse
}
