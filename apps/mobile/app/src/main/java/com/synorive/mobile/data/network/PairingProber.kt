package com.synorive.mobile.data.network

import com.synorive.mobile.data.model.HealthResponse
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * 配对页"测试连接"按钮专用。这时候地址还没存进 [com.synorive.mobile.data.datastore.PairingSettings]，
 * 走不了挂着 [DynamicBaseUrlInterceptor] 的正式 Retrofit 单例（它读的是已保存的状态），
 * 所以单开一个轻客户端直连候选地址，探的是 `/pairing/status`——不挂令牌闸，配对前就能验证。
 *
 * 🔴 **不是 `/health`。** `/health` 现在要求配对令牌了（它报 CPU/内存/DB 大小这些，
 * 不该在配对前、没有令牌的情况下就能被局域网里任何设备读到）。
 * `/pairing/status` 只报"这是不是 Synorive + 库里几条内容 + TLS 指纹"，
 * 字段名和 [HealthResponse] 兼容，探测逻辑不用跟着改。
 */
object PairingProber {
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .build()
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun probe(host: String, port: Int): Result<HealthResponse> = withContext(Dispatchers.IO) {
        runCatching {
            require(host.isNotBlank() && port in 1..65535) { "地址或端口不对" }
            val request = Request.Builder().url("http://$host:$port/pairing/status").get().build()
            client.newCall(request).execute().use { resp ->
                if (!resp.isSuccessful) error("连上了，但引擎返回 HTTP ${resp.code}")
                val text = resp.body?.string().orEmpty()
                json.decodeFromString(HealthResponse.serializer(), text)
            }
        }
    }
}
