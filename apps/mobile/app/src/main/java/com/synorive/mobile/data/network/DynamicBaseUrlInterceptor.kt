package com.synorive.mobile.data.network

import com.synorive.mobile.data.datastore.PairingStateHolder
import okhttp3.Interceptor
import okhttp3.Response

/**
 * Retrofit 要求 baseUrl 在构建那一刻就是个合法 URL，但配对的地址是用户运行时
 * 手动填的、还能随时在设置里改掉——所以 Retrofit 侧只给一个占位地址，
 * 每个请求真正发出去之前，这个拦截器用当前配对状态把 host/port 改写掉。
 *
 * 改的是请求本身的 URL（而不是加一个 Host 头），因为 OkHttp 真的是拿改写后的
 * host 去建 TCP 连接——只改头不改 URL 的话，流量还是会往占位地址那边发。
 */
class DynamicBaseUrlInterceptor(
    private val stateHolder: PairingStateHolder,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val state = stateHolder.current()

        if (!state.paired) {
            // 没配对时理论上不该有请求发出去（仓库/界面层应该先挡住）；
            // 真发生了就让它按占位地址走，调用方会拿到一个明确的连接失败，
            // 而不是悄悄把令牌带去一个错误的地方
            return chain.proceed(original)
        }

        val newUrl = original.url.newBuilder()
            .scheme("http")
            .host(state.host)
            .port(state.port)
            .build()

        val newRequest = original.newBuilder()
            .url(newUrl)
            .header("X-Synorive-Token", state.token)
            .build()

        return chain.proceed(newRequest)
    }
}
