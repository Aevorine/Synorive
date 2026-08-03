package com.synorive.mobile.ui.reverse

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel as composeViewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.synorive.mobile.LocalAppContainer

/** 从"最近看过"/搜索结果里点"反查来源"进来的——目标已经在库里，直接按 itemId 查。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReverseResultScreen(itemId: String, isVideo: Boolean, onBack: () -> Unit) {
    val container = LocalAppContainer.current
    val viewModel: ReverseResultViewModel = composeViewModel(
        factory = viewModelFactory { initializer { ReverseResultViewModel(container.engineRepository) } },
    )
    val uiState by viewModel.uiState.collectAsState()

    LaunchedEffect(itemId, isVideo) { viewModel.run(itemId, isVideo) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(if (isVideo) "视频反查（W6）" else "图片反查（W5）") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
            )
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize(), contentAlignment = Alignment.Center) {
            when (val state = uiState) {
                is ReverseLookupUiState.Loading -> CircularProgressIndicator()
                is ReverseLookupUiState.Failed -> Text(state.message, color = MaterialTheme.colorScheme.error)
                is ReverseLookupUiState.ImageOk -> {
                    val result = state.result
                    if (result.pagesIncluding.isEmpty() && result.visualSimilar.isEmpty()) {
                        Text("网上没查到相关结果")
                    } else {
                        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp)) {
                            reverseImageResultItems(result)
                        }
                    }
                }
                is ReverseLookupUiState.VideoOk -> {
                    val result = state.result
                    if (result.candidates.isEmpty()) {
                        Text("没找到可能的来源")
                    } else {
                        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp)) {
                            item {
                                Text(
                                    "试了 ${result.framesTried} 个关键帧",
                                    style = MaterialTheme.typography.bodySmall,
                                    modifier = Modifier.padding(bottom = 8.dp),
                                )
                            }
                            items(result.candidates) { c ->
                                Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                                    Text(c.title.ifBlank { c.pageUrl }, style = MaterialTheme.typography.titleMedium)
                                    Text(
                                        "${c.matchedKeyframes} 帧命中 · ${c.pageUrl}",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 1,
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
