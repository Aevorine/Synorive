package com.synorive.mobile.ui.search

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.item
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.TravelExplore
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel as composeViewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.synorive.mobile.LocalAppContainer
import com.synorive.mobile.data.local.CachedItemEntity
import com.synorive.mobile.data.model.SearchHit

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(
    onOpenCameraSearch: () -> Unit,
    onOpenPairing: () -> Unit,
    onOpenReverseSearch: (itemId: String, isVideo: Boolean) -> Unit,
) {
    val container = LocalAppContainer.current
    val viewModel: SearchViewModel = composeViewModel(
        factory = viewModelFactory { initializer { SearchViewModel(container.engineRepository) } },
    )

    val pairing by container.pairingSettings.state.collectAsState(initial = null)
    val query by viewModel.query.collectAsState()
    val uiState by viewModel.uiState.collectAsState()
    val recentCached by viewModel.recentCached.collectAsState()

    Scaffold(
        topBar = { TopAppBar(title = { Text("搜索") }) },
        floatingActionButton = {
            FloatingActionButton(onClick = onOpenCameraSearch) {
                Icon(Icons.Filled.CameraAlt, contentDescription = "拍照搜索")
            }
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            if (pairing?.paired == false) {
                Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    modifier = Modifier.fillMaxWidth().clickable(onClick = onOpenPairing),
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(14.dp),
                        horizontalArrangement = androidx.compose.foundation.layout.Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Filled.WifiOff, contentDescription = null)
                            Text(
                                "  还没配对桌面端，点这里去配对",
                                style = MaterialTheme.typography.bodyMedium,
                            )
                        }
                        Icon(Icons.Filled.ChevronRight, contentDescription = null)
                    }
                }
            }

            OutlinedTextField(
                value = query,
                onValueChange = viewModel::onQueryChange,
                modifier = Modifier.fillMaxWidth().padding(16.dp),
                placeholder = { Text("搜你存在这台电脑里的内容…") },
                leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
                singleLine = true,
            )

            when (val state = uiState) {
                is SearchUiState.Idle -> RecentCachedList(recentCached)
                is SearchUiState.Loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                is SearchUiState.Failed -> Column(Modifier.fillMaxSize()) {
                    Box(Modifier.fillMaxWidth().padding(16.dp)) {
                        Text(state.message, color = MaterialTheme.colorScheme.error)
                    }
                    RecentCachedList(recentCached)
                }
                is SearchUiState.Success -> if (state.hits.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("没搜到") }
                } else {
                    LazyColumn(contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp)) {
                        items(state.hits, key = { it.item.id }) { hit ->
                            SearchHitRow(hit, onReverseSearch = onOpenReverseSearch)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun RecentCachedList(items: List<CachedItemEntity>) {
    if (items.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("输入关键词开始搜索", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        return
    }
    LazyColumn(contentPadding = PaddingValues(horizontal = 16.dp, vertical = 4.dp)) {
        item {
            Text(
                "最近看过",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(vertical = 8.dp),
            )
        }
        items(items, key = { it.id }) { entry ->
            Column(Modifier.fillMaxWidth().padding(vertical = 10.dp)) {
                Text(entry.title.ifBlank { entry.locator }, style = MaterialTheme.typography.titleMedium)
                entry.snippet?.let {
                    Text(
                        stripHighlightTags(it),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                    )
                }
            }
        }
    }
}

/** 不是 private——[com.synorive.mobile.ui.reverse.ReverseResultScreen] 之类的地方也用到同一套渲染。
 *  onReverseSearch 非空且这条是图片/视频时，行尾露出"反查来源"（8.7/W5/W6）的入口。 */
@Composable
fun SearchHitRow(
    hit: SearchHit,
    onReverseSearch: ((itemId: String, isVideo: Boolean) -> Unit)? = null,
) {
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(hit.item.title.ifBlank { hit.item.locator }, style = MaterialTheme.typography.titleMedium)
            val snippet = hit.highlight ?: hit.item.snippet
            if (snippet != null) {
                Text(
                    text = highlightToAnnotated(snippet),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 3,
                )
            }
        }
        val modality = hit.item.modality
        if (onReverseSearch != null && (modality == "image" || modality == "video")) {
            IconButton(onClick = { onReverseSearch(hit.item.id, modality == "video") }) {
                Icon(
                    Icons.Filled.TravelExplore,
                    contentDescription = if (modality == "video") "视频反查来源" else "图片反查来源",
                )
            }
        }
    }
}

/** 服务端返回的高亮片段带 `<em>…</em>` 标记（D6/D7 命中词），转成加粗富文本。 */
private fun highlightToAnnotated(raw: String) = buildAnnotatedString {
    var rest = raw
    while (true) {
        val start = rest.indexOf("<em>")
        if (start < 0) {
            append(rest)
            break
        }
        append(rest.substring(0, start))
        val afterOpen = rest.substring(start + 4)
        val end = afterOpen.indexOf("</em>")
        if (end < 0) {
            append(afterOpen)
            break
        }
        withStyle(SpanStyle(fontWeight = FontWeight.Bold)) {
            append(afterOpen.substring(0, end))
        }
        rest = afterOpen.substring(end + 5)
    }
}

private fun stripHighlightTags(text: String): String =
    text.replace("<em>", "").replace("</em>", "")
