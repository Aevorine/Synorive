package com.synorive.mobile.ui.reverse

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.synorive.mobile.data.model.ReverseImageResult
import com.synorive.mobile.data.model.ReverseVideoResult
import com.synorive.mobile.data.repository.EngineRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed interface ReverseLookupUiState {
    data object Loading : ReverseLookupUiState
    data class ImageOk(val result: ReverseImageResult) : ReverseLookupUiState
    data class VideoOk(val result: ReverseVideoResult) : ReverseLookupUiState
    data class Failed(val message: String) : ReverseLookupUiState
}

/** W5/W6 对**库里已有条目**的反查——从搜索结果里点"反查来源"用这个。 */
class ReverseResultViewModel(private val repository: EngineRepository) : ViewModel() {
    private val _uiState = MutableStateFlow<ReverseLookupUiState>(ReverseLookupUiState.Loading)
    val uiState: StateFlow<ReverseLookupUiState> = _uiState

    fun run(itemId: String, isVideo: Boolean) {
        _uiState.value = ReverseLookupUiState.Loading
        viewModelScope.launch {
            if (isVideo) {
                repository.reverseVideoByItemId(itemId).fold(
                    onSuccess = { r ->
                        _uiState.value = if (r.error != null) {
                            ReverseLookupUiState.Failed(r.error)
                        } else {
                            ReverseLookupUiState.VideoOk(r)
                        }
                    },
                    onFailure = { e -> _uiState.value = ReverseLookupUiState.Failed(e.message ?: "反查失败") },
                )
            } else {
                repository.reverseImageByItemId(itemId).fold(
                    onSuccess = { r ->
                        _uiState.value = if (r.error != null) {
                            ReverseLookupUiState.Failed(r.error)
                        } else {
                            ReverseLookupUiState.ImageOk(r)
                        }
                    },
                    onFailure = { e -> _uiState.value = ReverseLookupUiState.Failed(e.message ?: "反查失败") },
                )
            }
        }
    }
}
