package com.synorive.mobile

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.IntentCompat
import com.synorive.mobile.navigation.SynoriveApp
import com.synorive.mobile.ui.share.ShareInboxViewModel
import com.synorive.mobile.ui.theme.SynoriveTheme

/**
 * 单 Activity + 单 ViewModel 承接分享意图。`singleTop`（见 AndroidManifest）
 * 意味着 App 已经在前台时再次分享，走的是 [onNewIntent] 而不是重新创建一个实例——
 * 两条路径都要喂给同一个 [shareInbox]，不然会出现"App 开着时分享没反应"的假象。
 */
class MainActivity : ComponentActivity() {
    private val shareInbox: ShareInboxViewModel by viewModels()

    /**
     * 长按桌面图标进来的快捷方式要落到哪一页。null = 正常启动。
     *
     * 🔴 **用 mutableStateOf 而不是普通字段。** 普通字段在 onNewIntent 里改了
     *    Compose 不会重组 —— 表现是"App 已经开着时，长按图标点拍照搜索没反应"。
     *    那种失败只在特定顺序下出现，最容易漏测。
     */
    private val pendingRoute = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handleIncomingIntent(intent)

        val container = (application as SynoriveApplication).container
        setContent {
            CompositionLocalProvider(LocalAppContainer provides container) {
                SynoriveTheme {
                    SynoriveApp(
                        shareInbox = shareInbox,
                        pendingRoute = pendingRoute.value,
                        onRouteConsumed = { pendingRoute.value = null },
                    )
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIncomingIntent(intent)
    }

    private fun handleIncomingIntent(intent: Intent?) {
        if (intent == null) return
        // 快捷方式（长按桌面图标）：直达某一页
        intent.getStringExtra("synorive.route")?.let { pendingRoute.value = it }
        if (intent.action != Intent.ACTION_SEND) return
        val text = intent.getStringExtra(Intent.EXTRA_TEXT)
        val uri = IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)
        shareInbox.receive(text = text, uri = uri, mimeType = intent.type)
    }
}
