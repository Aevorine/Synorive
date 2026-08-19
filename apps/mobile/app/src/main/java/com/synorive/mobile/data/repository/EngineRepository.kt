package com.synorive.mobile.data.repository

import android.content.Context
import android.net.Uri
import com.synorive.mobile.data.local.CachedItemDao
import com.synorive.mobile.data.local.CachedItemEntity
import com.synorive.mobile.data.model.IngestJob
import com.synorive.mobile.data.model.IngestRequest
import com.synorive.mobile.data.model.ReverseImageApiRequest
import com.synorive.mobile.data.model.ReverseImageResult
import com.synorive.mobile.data.model.ReverseVideoApiRequest
import com.synorive.mobile.data.model.ReverseVideoResult
import com.synorive.mobile.data.model.SearchRequest
import com.synorive.mobile.data.model.SearchResponse
import com.synorive.mobile.data.network.EngineApi
import com.synorive.mobile.data.network.UploadUtils
import kotlinx.coroutines.flow.Flow

/**
 * 手机端唯一的数据出口。所有真正的检索/分析都是转发给桌面引擎——
 * 这里不做任何"手机本地也算一遍"的事，那是"半独立"架构明确划掉的范围。
 */
class EngineRepository(
    private val api: EngineApi,
    private val cacheDao: CachedItemDao,
) {
    suspend fun search(query: String, limit: Int = 30): Result<SearchResponse> = runCatching {
        val resp = api.search(SearchRequest(query = query, limit = limit))
        if (resp.hits.isNotEmpty()) {
            val now = System.currentTimeMillis()
            cacheDao.upsertAll(
                resp.hits.map { hit ->
                    CachedItemEntity(
                        id = hit.item.id,
                        title = hit.item.title,
                        snippet = hit.item.snippet ?: hit.highlight,
                        modality = hit.item.modality,
                        locator = hit.item.locator,
                        score = hit.score,
                        query = query,
                        cachedAt = now,
                    )
                },
            )
        }
        resp
    }

    /** 分享/拍照进来的文件：先传到引擎那台机器落盘，再当普通目标投喂进库。 */
    suspend fun ingestUri(context: Context, uri: Uri, tags: List<String>? = null): Result<IngestJob> = runCatching {
        val file = UploadUtils.copyToCache(context, uri)
        try {
            val part = UploadUtils.toMultipart(context, file, uri)
            val uploaded = api.upload(part)
            api.ingest(IngestRequest(targets = listOf(uploaded.path), tags = tags))
        } finally {
            file.delete()
        }
    }

    /** W5：拍的/分享进来的图片直接反查"网上哪还有这张图"——不落库，只是临时传上去查一次。 */
    suspend fun reverseImageByUri(context: Context, uri: Uri, limit: Int = 20): Result<ReverseImageResult> = runCatching {
        val file = UploadUtils.copyToCache(context, uri)
        try {
            val part = UploadUtils.toMultipart(context, file, uri)
            val uploaded = api.upload(part)
            api.reverseImage(ReverseImageApiRequest(path = uploaded.path, limit = limit))
        } finally {
            file.delete()
        }
    }

    /** W5：对库里已有的一条图片内容反查来源。 */
    suspend fun reverseImageByItemId(itemId: String, limit: Int = 20): Result<ReverseImageResult> = runCatching {
        api.reverseImage(ReverseImageApiRequest(itemId = itemId, limit = limit))
    }

    /** W6：对库里已有、且已经跑完场景检测的一条视频反查来源。 */
    suspend fun reverseVideoByItemId(itemId: String, maxFrames: Int = 5): Result<ReverseVideoResult> = runCatching {
        api.reverseVideo(ReverseVideoApiRequest(itemId = itemId, maxFrames = maxFrames))
    }

    /** 纯文本/链接分享：不用先上传，字符串本身就是合法目标（ingest.web.is_url 会认出链接）。 */
    suspend fun ingestText(text: String, tags: List<String>? = null): Result<IngestJob> = runCatching {
        api.ingest(IngestRequest(targets = listOf(text), tags = tags))
    }

    fun recentCached(limit: Int = 100): Flow<List<CachedItemEntity>> = cacheDao.recent(limit)

    /**
     * 离线检索：连不上电脑时，在缓存过的内容里找。
     *
     * 🔴 **它不是"降级的搜索"，是"另一件事"。** 缓存里只有最近搜过的那几百条，
     *    既没有语义召回也没有全文索引。所以调用方**必须**把结果标成离线的，
     *    并且把"内容截至某时刻"显示出来 —— 不标的话，用户会把
     *    "缓存里没有"当成"库里没有"，然后得出一个错的结论。
     */
    suspend fun searchOffline(query: String, limit: Int = 50): OfflineResult {
        val hits = cacheDao.searchOffline(query.trim(), limit)
        return OfflineResult(hits = hits, newestCachedAt = cacheDao.newestCachedAt(), total = cacheDao.count())
    }

    data class OfflineResult(
        val hits: List<CachedItemEntity>,
        /** 缓存里最新那条的时间戳。null = 缓存是空的 */
        val newestCachedAt: Long?,
        /** 缓存里一共多少条 —— 用来说清"你能离线搜的范围有多大" */
        val total: Int,
    )

    suspend fun clearCache() = cacheDao.clear()
}
