package com.synorive.mobile.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(entities = [CachedItemEntity::class], version = 1, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    abstract fun cachedItemDao(): CachedItemDao

    companion object {
        @Volatile
        private var instance: AppDatabase? = null

        fun get(context: Context): AppDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "synorive-cache.db",
                )
                    // 纯缓存，不是真相源——库结构以后要改，直接丢重建比手写迁移划算
                    .fallbackToDestructiveMigration()
                    .build()
                    .also { instance = it }
            }
    }
}
