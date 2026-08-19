package com.synorive.mobile.data.network

import com.synorive.mobile.BuildConfig
import com.synorive.mobile.data.datastore.PairingStateHolder
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import java.util.concurrent.TimeUnit

/** 手搓的最小 DI——这个 App 就三五个仓库/视图模型，Hilt 那一套注解处理器
 *  换来的收益压不过它加的构建复杂度。 */
object NetworkModule {
    fun buildApi(stateHolder: PairingStateHolder): EngineApi {
        val json = Json {
            ignoreUnknownKeys = true // 服务端字段以后会加，手机端不用逐字段跟着改
            coerceInputValues = true
        }

        val builder = OkHttpClient.Builder()
            .addInterceptor(DynamicBaseUrlInterceptor(stateHolder))

        // 🔴 正式包一行网络日志都不打。Logcat 在同一台手机上是**任何已装应用都能读**
        // 的（有 READ_LOGS 的调试工具、厂商预装的日志助手），而 BASIC 级别会把
        // 请求 URL、耗时、响应大小写进去 —— 那是"这个人什么时候搜了什么"的作息画像。
        // 调试包留着，排障需要它。
        if (BuildConfig.DEBUG) {
            builder.addInterceptor(
                HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC },
            )
        }

        val client = builder
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            // 传视频/大图给 /api/upload 可能要久一点，别提前掐断
            .writeTimeout(120, TimeUnit.SECONDS)
            .build()

        val retrofit = Retrofit.Builder()
            // 占位地址，真正的 host:port 由 DynamicBaseUrlInterceptor 每次请求改写
            .baseUrl("http://127.0.0.1/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json; charset=UTF-8".toMediaType()))
            .build()

        return retrofit.create(EngineApi::class.java)
    }
}
