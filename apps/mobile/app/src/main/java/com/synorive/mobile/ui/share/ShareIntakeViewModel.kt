package com.synorive.mobile.ui.share

import android.content.Context
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.synorive.mobile.data.model.ReverseImageResult
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
                onSuccess = { _ingestState.value = ShareOpState.Done("已加入资料库") },
                onFailure = { e -> _ingestState.value = ShareOpState.Failed(e.message ?: "投喂失败") },
            )
        }
    }

    fun ingestUri(context: Context, uri: Uri) {
        _ingestState.value = ShareOpState.Working
        viewModelScope.launch {
            repository.ingestUri(context, uri).fold(
                onSuccess = { _ingestState.value = ShareOpState.Done("已上传并加入资料库") },
                onFailure = { e -> _ingestState.value = ShareOpState.Failed(e.message ?: "投喂失败") },
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
