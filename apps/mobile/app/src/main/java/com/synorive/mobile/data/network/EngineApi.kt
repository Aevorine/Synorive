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
}
