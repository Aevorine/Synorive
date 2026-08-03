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

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(items: List<CachedItemEntity>)

    @Query("DELETE FROM cached_items WHERE cachedAt < :beforeMillis")
    suspend fun pruneOlderThan(beforeMillis: Long)

    @Query("DELETE FROM cached_items")
    suspend fun clear()
}
