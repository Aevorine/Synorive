package com.synorive.mobile

import android.app.Application
import com.synorive.mobile.data.datastore.PairingSettings
import com.synorive.mobile.data.datastore.PairingStateHolder
import com.synorive.mobile.data.local.AppDatabase
import com.synorive.mobile.data.network.NetworkModule
import com.synorive.mobile.data.repository.EngineRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * 手搓的最小依赖容器。这个 App 加起来就一个仓库、几个视图模型——
 * Hilt/Koin 那一套注解处理器和运行时反射换来的收益，压不过它们
 * 加给这么小一个项目的构建时间和排障成本。
 */
class AppContainer(app: Application) {
    val pairingSettings = PairingSettings(app)
    val pairingStateHolder = PairingStateHolder()

    private val database = AppDatabase.get(app)
    private val api = NetworkModule.buildApi(pairingStateHolder)

    val engineRepository = EngineRepository(api, database.cachedItemDao())

    /** 把持久化的配对状态同步进内存里的 [pairingStateHolder]——网络拦截器读的是后者，
     *  同步之前它一直是空状态，任何请求都会被当成"没配对"处理。 */
    fun startSync(scope: CoroutineScope) {
        scope.launch {
            pairingSettings.state.collect { pairingStateHolder.update(it) }
        }
    }
}
