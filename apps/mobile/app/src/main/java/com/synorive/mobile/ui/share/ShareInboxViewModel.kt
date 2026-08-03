package com.synorive.mobile.ui.share

import android.net.Uri
import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

data class SharedContent(
    val text: String? = null,
    val uri: Uri? = null,
    val mimeType: String? = null,
)

/**
 * MainActivity 收到系统的 `ACTION_SEND` 时把内容塞进这里，[ShareIntakeScreen] 订阅并展示
 * "确认要投喂/反查吗"。Activity 级 ViewModel——分享是打开这个 Activity 的唯一场景，
 * 不需要跨 Activity 共享，也不需要在 Activity 销毁后继续存活。
 */
class ShareInboxViewModel : ViewModel() {
    private val _pending = MutableStateFlow<SharedContent?>(null)
    val pending: StateFlow<SharedContent?> = _pending

    fun receive(text: String?, uri: Uri?, mimeType: String?) {
        if (text.isNullOrBlank() && uri == null) return
        _pending.value = SharedContent(text?.takeIf { it.isNotBlank() }, uri, mimeType)
    }

    fun consume() {
        _pending.value = null
    }
}
