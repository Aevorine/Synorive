package com.synorive.mobile.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface CachedItemDao {
    @Query("SELECT * FROM cached_items ORDER BY cachedAt DESC LIMIT :limit")
    fun recent(limit: Int = 100): Flow<List<CachedItemEntity>>

    /**
     * 离线检索：在缓存过的内容里按标题/摘录找。
     *
     * 只做 LIKE 子串匹配，**不假装它等于真检索** —— 缓存里只有你最近搜过的
     * 那几百条，既没有语义召回也没有全文索引。界面上必须说清"这是离线结果"，
     * 让用户知道"没搜到"可能只是因为那条还没被缓存过。
     */
    @Query(
        """
        SELECT * FROM cached_items
        WHERE title LIKE '%' || :q || '%' OR snippet LIKE '%' || :q || '%'
        ORDER BY cachedAt DESC
        LIMIT :limit
        """,
    )
    suspend fun searchOffline(q: String, limit: Int = 50): List<CachedItemEntity>

    /** 缓存里最新那条是什么时候存的 —— 界面要说"内容截至某时刻" */
    @Query("SELECT MAX(cachedAt) FROM cached_items")
    suspend fun newestCachedAt(): Long?

    @Query("SELECT COUNT(*) FROM cached_items")
    suspend fun count(): Int

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(items: List<CachedItemEntity>)

    @Query("DELETE FROM cached_items WHERE cachedAt < :beforeMillis")
    suspend fun pruneOlderThan(beforeMillis: Long)

    @Query("DELETE FROM cached_items")
    suspend fun clear()
}
