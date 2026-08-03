package com.synorive.mobile.ui.camera

import android.content.Context
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.synorive.mobile.data.model.ReverseImageResult
import com.synorive.mobile.data.repository.EngineRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed interface ReverseImageUiState {
    data object Idle : ReverseImageUiState
    data object Loading : ReverseImageUiState
    data class Ok(val result: ReverseImageResult) : ReverseImageUiState
    data class Failed(val message: String) : ReverseImageUiState
}

/** W5：拍一张照片，反查"网上哪还出现过这张图"（出处/更高清版/搬运源）。 */
class CameraSearchViewModel(private val repository: EngineRepository) : ViewModel() {
    private val _uiState = MutableStateFlow<ReverseImageUiState>(ReverseImageUiState.Idle)
    val uiState: StateFlow<ReverseImageUiState> = _uiState

    fun reverseSearch(context: Context, uri: Uri) {
        _uiState.value = ReverseImageUiState.Loading
        viewModelScope.launch {
            repository.reverseImageByUri(context, uri).fold(
                onSuccess = { result ->
                    _uiState.value = if (result.error != null) {
                        ReverseImageUiState.Failed(result.error)
                    } else {
                        ReverseImageUiState.Ok(result)
                    }
                },
                onFailure = { e -> _uiState.value = ReverseImageUiState.Failed(e.message ?: "反查失败") },
            )
        }
    }
}
