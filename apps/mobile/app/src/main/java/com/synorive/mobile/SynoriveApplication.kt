package com.synorive.mobile

import android.app.Application
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

class SynoriveApplication : Application() {
    lateinit var container: AppContainer
        private set

    /** App 存活期的协程作用域——只用来把 DataStore 里的配对状态同步进内存持有者，
     *  不跟着任何一个 Activity/Composable 的生命周期走。 */
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        container.startSync(appScope)
    }
}
