package com.synorive.mobile.ui.search

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.synorive.mobile.data.model.SearchHit
import com.synorive.mobile.data.local.CachedItemEntity
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

    /**
     * 连不上电脑，退回在缓存里找到的结果。
     *
     * 🔴 **和 Success 分成两个状态，不合并。** 合并的话界面没法区分
     *    "这是刚从电脑上查的" 和 "这是本地缓存里的旧的" ——
     *    而这两件事对用户的判断完全不同：缓存里搜不到只说明"还没缓存过"，
     *    不说明"库里没有"。把它们混在一起会让人得出错的结论。
     */
    data class Offline(
        val hits: List<CachedItemEntity>,
        /** 缓存里最新那条是什么时候存的。null = 缓存是空的 */
        val newestCachedAt: Long?,
        /** 缓存一共多少条 —— 说清"能离线搜的范围有多大" */
        val cachedTotal: Int,
        /** 连不上的原因，照实说 */
        val reason: String,
    ) : SearchUiState
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
                    onFailure = { e ->
                        // 连不上电脑时先去缓存里找一遍，**但要明确标成离线结果**。
                        // 缓存里一条都没有才报失败 —— 那时候确实没别的可给了
                        val off = runCatching { repository.searchOffline(q) }.getOrNull()
                        _uiState.value = if (off != null && off.hits.isNotEmpty()) {
                            SearchUiState.Offline(
                                hits = off.hits,
                                newestCachedAt = off.newestCachedAt,
                                cachedTotal = off.total,
                                reason = e.message ?: "连不上电脑",
                            )
                        } else {
                            SearchUiState.Failed(e.message ?: "搜索失败")
                        }
                    },
                )
            }
        }
    }

    fun onQueryChange(q: String) {
        _query.value = q
    }
}
