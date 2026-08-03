package com.synorive.mobile.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

// 🔴 加了 SyncOpEntity 就**必须把 version 往上抬**。不抬的话 Room 会拿着
// 旧 schema 去开新库，抛 IllegalStateException 直接崩在启动路径上。
// 这里配的是 fallbackToDestructiveMigration，所以升版本 = 丢缓存重建，
// 对纯缓存表没问题 —— 但 sync_ops **不是缓存**，它装着还没推出去的改动。
// 见下面 companion object 里的说明
@Database(
    entities = [CachedItemEntity::class, SyncOpEntity::class],
    version = 2,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun cachedItemDao(): CachedItemDao
    abstract fun syncOpDao(): SyncOpDao

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
                    // ⚠️ **这条以后要改。** 原来这个库只有缓存表，丢了重建无所谓。
                    // 现在多了 `sync_ops` —— 那里面是**还没推给桌面端的本地改动**，
                    // 破坏性迁移会把它们直接删掉，用户离线写的东西无声消失。
                    // 当前 v1→v2 是新增表，Room 的破坏性迁移仍会重建整库，
                    // 所以**升级到 v2 的那一次会丢掉旧缓存**（可接受，缓存本来就能重拉）。
                    // 再往后加字段时必须改成手写 Migration，不能继续用这个。
                    .fallbackToDestructiveMigration()
                    .build()
                    .also { instance = it }
            }
    }
}
