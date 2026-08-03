package com.synorive.mobile.data.local

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * "半独立"架构的落地（I2 决策）：手机端不是一套并行的检索引擎，只是给
 * 最近看过的结果留一份离线可见的轻量缓存——飞机上/信号差的时候能翻,
 * 真正的搜索永远打给桌面引擎。
 */
@Entity(tableName = "cached_items")
data class CachedItemEntity(
    @PrimaryKey val id: String,
    val title: String,
    val snippet: String?,
    val modality: String,
    val locator: String,
    val score: Double,
    /** 命中这条时用的搜索词，最近记录按它分组展示 */
    val query: String,
    val cachedAt: Long,
)
