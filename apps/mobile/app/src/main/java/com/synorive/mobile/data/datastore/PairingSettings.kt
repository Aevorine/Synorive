package com.synorive.mobile.data.datastore

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.map

private val Context.pairingDataStore by preferencesDataStore(name = "pairing")

/**
 * 一次配对的全部信息：地址、端口、令牌。
 *
 * "半独立"架构（I2 决策）的落地方式很朴素——不做局域网自动发现（mDNS/UDP 广播
 * 在部分路由器/公共 Wi-Fi 上会被挡，稳定性不如"手动填一次，存起来"），
 * 用户在桌面端设置页的"安卓配对"面板里看到地址和令牌，手动填进来。
 */
data class PairingState(
    val host: String = "",
    val port: Int = 0,
    val token: String = "",
) {
    val paired: Boolean get() = host.isNotBlank() && port > 0 && token.isNotBlank()
    val baseUrl: String get() = "http://$host:$port/"
}

/** 持久化层：读写 DataStore。UI 直接订阅 `state`；网络层走下面的 [PairingStateHolder]。 */
class PairingSettings(private val context: Context) {
    private object Keys {
        val HOST = stringPreferencesKey("host")
        val PORT = intPreferencesKey("port")
        val TOKEN = stringPreferencesKey("token")
    }

    val state: Flow<PairingState> = context.pairingDataStore.data.map { prefs ->
        PairingState(
            host = prefs[Keys.HOST] ?: "",
            port = prefs[Keys.PORT] ?: 0,
            token = prefs[Keys.TOKEN] ?: "",
        )
    }

    suspend fun save(host: String, port: Int, token: String) {
        context.pairingDataStore.edit { prefs ->
            prefs[Keys.HOST] = host.trim()
            prefs[Keys.PORT] = port
            prefs[Keys.TOKEN] = token.trim()
        }
    }

    suspend fun clear() {
        context.pairingDataStore.edit { it.clear() }
    }
}

/**
 * 网络拦截器要**同步**读到当前配对状态（每个请求都要读，不能每次都挂起等 DataStore
 * 那次磁盘 I/O）。这个持有者在 App 启动时订阅一次 [PairingSettings.state]，
 * 之后所有读取都是内存里的 `StateFlow.value`，零 I/O。
 */
class PairingStateHolder {
    private val flow = MutableStateFlow(PairingState())

    fun current(): PairingState = flow.value

    fun update(state: PairingState) {
        flow.value = state
    }
}
