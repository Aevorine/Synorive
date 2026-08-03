package com.synorive.mobile.data.network

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

        val logging = HttpLoggingInterceptor().apply {
            // BASIC 就够排障用了——BODY 级别会把资料原文整段打进 Logcat，不合适
            level = HttpLoggingInterceptor.Level.BASIC
        }

        val client = OkHttpClient.Builder()
            .addInterceptor(DynamicBaseUrlInterceptor(stateHolder))
            .addInterceptor(logging)
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
