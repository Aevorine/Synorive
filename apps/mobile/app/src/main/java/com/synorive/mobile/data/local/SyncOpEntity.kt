package com.synorive.mobile.data.local

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query

/**
 * 6.5 —— 手机侧的离线操作队列。
 *
 * 地铁里没信号也能写，联网了再批量推。
 *
 * 🔴 **`lamport` 和 `device` 是冲突判定的全部依据，`wallTs` 只用来显示。**
 * 手机的系统时间和电脑差几分钟是常态（没校时、时区错、用户手动改过），
 * 按挂钟判的话时间偏快的那台会永远赢，它的旧数据会持续覆盖另一台的新数据 ——
 * 而且完全不报错，用户只会觉得"我明明改了怎么又变回去了"。
 *
 * 🔴 **`sent` 只在对端确认之后才置 1。** 发出去就置 1 的话，
 * 网络在半路断了这批操作永远不会重发 —— 数据静默丢失，两端看起来都正常。
 */
@Entity(tableName = "sync_ops")
data class SyncOpEntity(
    @PrimaryKey val id: String,
    val entity: String,
    val entityId: String,
    /** upsert / delete */
    val kind: String,
    /** JSON 字符串。Room 不存复杂对象，序列化留给上层 */
    val payload: String,
    val device: String,
    val lamport: Long,
    val wallTs: Long,
    val sent: Boolean = false,
)

@Dao
interface SyncOpDao {
    @Insert
    suspend fun insert(op: SyncOpEntity)

    /**
     * 待推的操作。**按 lamport 再按 id 排序** ——
     * 只按 lamport 的话同一逻辑时刻的多条顺序不定，
     * 两次拉取给出不同的顺序，对端的去重日志会变得没法读。
     */
    @Query("SELECT * FROM sync_ops WHERE sent = 0 ORDER BY lamport ASC, id ASC LIMIT :limit")
    suspend fun pending(limit: Int = 200): List<SyncOpEntity>

    @Query("UPDATE sync_ops SET sent = 1 WHERE id IN (:ids)")
    suspend fun markSent(ids: List<String>): Int

    @Query("SELECT COUNT(*) FROM sync_ops WHERE sent = 0")
    suspend fun pendingCount(): Int

    /** 清掉已确认的历史，留最近若干条备查 */
    @Query(
        "DELETE FROM sync_ops WHERE sent = 1 AND id NOT IN " +
            "(SELECT id FROM sync_ops WHERE sent = 1 ORDER BY lamport DESC LIMIT :keep)",
    )
    suspend fun purgeSent(keep: Int = 500): Int

    @Query("SELECT MAX(lamport) FROM sync_ops")
    suspend fun maxLamport(): Long?
}
