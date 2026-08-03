package com.synorive.mobile.ui.search

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.synorive.mobile.data.model.SearchHit
import com.synorive.mobile.data.repository.EngineRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

sealed interface SearchUiState {
    data object Idle : SearchUiState
    data object Loading : SearchUiState
    data class Success(val hits: List<SearchHit>, val elapsedMs: Double) : SearchUiState
    data class Failed(val message: String) : SearchUiState
}

class SearchViewModel(private val repository: EngineRepository) : ViewModel() {
    private val _query = MutableStateFlow("")
    val query: StateFlow<String> = _query

    private val _uiState = MutableStateFlow<SearchUiState>(SearchUiState.Idle)
    val uiState: StateFlow<SearchUiState> = _uiState

    /** 没配对，或这次搜索本身失败时，界面退回显示这份最近缓存——总比一片空白强。 */
    val recentCached = repository.recentCached().stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        emptyList(),
    )

    init {
        viewModelScope.launch {
            // 300ms 防抖：边打字边搜，但不是每敲一个字就打一次局域网请求
            _query.debounce(300).collectLatest { q ->
                if (q.isBlank()) {
                    _uiState.value = SearchUiState.Idle
                    return@collectLatest
                }
                _uiState.value = SearchUiState.Loading
                repository.search(q).fold(
                    onSuccess = { resp -> _uiState.value = SearchUiState.Success(resp.hits, resp.elapsedMs) },
                    onFailure = { e -> _uiState.value = SearchUiState.Failed(e.message ?: "搜索失败") },
                )
            }
        }
    }

    fun onQueryChange(q: String) {
        _query.value = q
    }
}
