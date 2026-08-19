package com.synorive.mobile.ui.share

import android.content.Context
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.synorive.mobile.data.model.ReverseImageResult
import com.synorive.mobile.data.model.IngestJob
import com.synorive.mobile.data.repository.EngineRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed interface ShareOpState {
    data object Idle : ShareOpState
    data object Working : ShareOpState
    data class Done(val message: String) : ShareOpState
    data class Failed(val message: String) : ShareOpState
}

/** 分享投喂页背后的动作：存入资料库（任意内容），或者对图片顺带反查来源（W5）。
 *  两个动作互相独立——反查不代表要存，存了也不代表要反查，界面上是两个平级按钮。 */
class ShareIntakeViewModel(private val repository: EngineRepository) : ViewModel() {
    private val _ingestState = MutableStateFlow<ShareOpState>(ShareOpState.Idle)
    val ingestState: StateFlow<ShareOpState> = _ingestState

    private val _reverseOpState = MutableStateFlow<ShareOpState>(ShareOpState.Idle)
    val reverseOpState: StateFlow<ShareOpState> = _reverseOpState

    private val _reverseResult = MutableStateFlow<ReverseImageResult?>(null)
    val reverseResult: StateFlow<ReverseImageResult?> = _reverseResult

    fun ingestText(text: String) {
        _ingestState.value = ShareOpState.Working
        viewModelScope.launch {
            repository.ingestText(text).fold(
                onSuccess = { job -> _ingestState.value = ShareOpState.Done(receipt(job)) },
                onFailure = { e -> _ingestState.value = ShareOpState.Failed(explain(e)) },
            )
        }
    }

    fun ingestUri(context: Context, uri: Uri) {
        _ingestState.value = ShareOpState.Working
        viewModelScope.launch {
            repository.ingestUri(context, uri).fold(
                onSuccess = { job -> _ingestState.value = ShareOpState.Done("已上传。" + receipt(job)) },
                onFailure = { e -> _ingestState.value = ShareOpState.Failed(explain(e)) },
            )
        }
    }

    fun reverseImage(context: Context, uri: Uri) {
        _reverseOpState.value = ShareOpState.Working
        viewModelScope.launch {
            repository.reverseImageByUri(context, uri).fold(
                onSuccess = { r ->
                    if (r.error != null) {
                        _reverseOpState.value = ShareOpState.Failed(r.error)
                    } else {
                        _reverseResult.value = r
                        _reverseOpState.value = ShareOpState.Done("反查完成")
                    }
                },
                onFailure = { e -> _reverseOpState.value = ShareOpState.Failed(e.message ?: "反查失败") },
            )
        }
    }
}

/**
 * 投喂回执。
 *
 * 🔴 **要说清"存进去几条"，不能只说"已加入资料库"。**
 *    分享一个网页进来，引擎可能抓到 1 条正文、也可能因为登录墙抓到 0 条 ——
 *    两种情况下"已加入资料库"这句话都会显示出来，而后者其实什么都没进去。
 *    用户过几天回去搜发现没有，只会以为是搜索坏了。
 */
private fun receipt(job: IngestJob): String = when {
    job.totalItems > 0 -> "已存入 ${job.totalItems} 条，回电脑上就能搜到。"
    // 🔴 0 条要明说。引擎接了任务但没解析出内容是很常见的一类结果
    //    （登录墙、纯图片页、格式不支持），而它**不是错误**，所以不能报成失败
    else -> "引擎收下了，但这一份没解析出可索引的内容（常见于要登录的网页、纯图片页）。" +
        "回电脑上看「分析中心」能看到它的处理结果。"
}

/** 失败也要说清是哪一类，而不是把异常消息原样甩出来 */
private fun explain(e: Throwable): String {
    val raw = e.message ?: "投喂失败"
    return when {
        raw.contains("Unable to resolve host", true) ||
            raw.contains("Failed to connect", true) ||
            raw.contains("timeout", true) ->
            "连不上电脑。确认两台设备在同一个 Wi-Fi、电脑上的 Synorive 开着、" +
                "而且配对里的端口没变（端口每次启动都会变）。"
        raw.contains("401") -> "配对令牌不对。回电脑上「设置 → 安卓配对」重新扫一次码。"
        else -> raw
    }
}