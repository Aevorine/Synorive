package com.synorive.mobile.ui.search

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
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
                is SearchUiState.Offline -> Column(Modifier.fillMaxSize()) {
                    // 🔴 **必须一眼看出这是离线结果。** 缓存里搜不到只说明"还没缓存过"，
                    //    不说明"库里没有" —— 不标出来的话用户会得出错的结论。
                    Surface(
                        color = MaterialTheme.colorScheme.secondaryContainer,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Column(Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Filled.WifiOff, contentDescription = null)
                                Spacer(Modifier.width(8.dp))
                                Text(
                                    "离线结果（连不上电脑）",
                                    style = MaterialTheme.typography.titleSmall,
                                )
                            }
                            Text(
                                buildString {
                                    append("只在这台手机缓存的 ")
                                    append(state.cachedTotal)
                                    append(" 条里找。")
                                    state.newestCachedAt?.let {
                                        append("内容截至 ")
                                        append(
                                            java.text.SimpleDateFormat("MM-dd HH:mm", java.util.Locale.getDefault())
                                                .format(java.util.Date(it)),
                                        )
                                        append("。")
                                    }
                                    append("这里搜不到不代表电脑上没有。")
                                },
                                style = MaterialTheme.typography.bodySmall,
                            )
                            Text(
                                state.reason,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    LazyColumn(contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp)) {
                        items(state.hits, key = { it.id }) { CachedRow(it) }
                    }
                }
                is SearchUiState.Success -> if (state.hits.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("没搜到") }
                } else {
                    ResultsPane(state.hits, onOpenReverseSearch)
                }
            }
        }
    }
}

/**
 * 结果区。窄屏一栏，宽屏（平板横屏 / 折叠屏展开）两栏。
 *
 * 🔴 **断点按可用宽度定，不按"是不是平板"。** 判机型的话，
 *    平板竖屏、手机横屏、分屏多窗口这几种情况全都会判错 ——
 *    而判错的表现是"在我的设备上布局很怪"，用户没法自己解释。
 *    BoxWithConstraints 读到的是**这一块真实拿到的宽度**，
 *    分屏、折叠、旋转都自动成立，而且不用引 window-size-class 依赖。
 *
 * 🔴 **选中项要在旋转/分屏之后活下来**（rememberSaveable）。
 *    横竖屏一转选中就没了，是这类双栏布局最常见也最烦人的一个毛病。
 */
@Composable
private fun ResultsPane(
    hits: List<SearchHit>,
    onOpenReverseSearch: (itemId: String, isVideo: Boolean) -> Unit,
) {
    BoxWithConstraints(Modifier.fillMaxSize()) {
        // 720dp：一栏列表 + 一栏详情各自还能有 ~350dp，再窄就两边都挤
        val twoPane = maxWidth >= 720.dp
        var selectedId by rememberSaveable { mutableStateOf<String?>(null) }
        val selected = hits.firstOrNull { it.item.id == selectedId } ?: hits.firstOrNull()

        if (!twoPane) {
            LazyColumn(contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp)) {
                items(hits, key = { it.item.id }) { hit ->
                    SearchHitRow(hit, onReverseSearch = onOpenReverseSearch)
                }
            }
            return@BoxWithConstraints
        }

        Row(Modifier.fillMaxSize()) {
            LazyColumn(
                modifier = Modifier.weight(1f).fillMaxHeight(),
                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            ) {
                items(hits, key = { it.item.id }) { hit ->
                    val on = hit.item.id == selected?.item?.id
                    Surface(
                        color = if (on) {
                            MaterialTheme.colorScheme.secondaryContainer
                        } else {
                            MaterialTheme.colorScheme.surface
                        },
                        modifier = Modifier.fillMaxWidth().clickable { selectedId = hit.item.id },
                    ) {
                        Box(Modifier.padding(horizontal = 8.dp)) {
                            SearchHitRow(hit, onReverseSearch = onOpenReverseSearch)
                        }
                    }
                }
            }

            VerticalDivider()

            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxHeight()
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                val hit = selected
                if (hit == null) {
                    Text("左边点一条看详情", color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    Text(
                        hit.item.title.ifBlank { hit.item.locator },
                        style = MaterialTheme.typography.titleLarge,
                    )
                    Text(
                        hit.item.locator,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    (hit.highlight ?: hit.item.snippet)?.let {
                        Text(highlightToAnnotated(it), style = MaterialTheme.typography.bodyMedium)
                    }
                    val modality = hit.item.modality
                    if (modality == "image" || modality == "video") {
                        Button(onClick = { onOpenReverseSearch(hit.item.id, modality == "video") }) {
                            Text(if (modality == "video") "反查视频来源" else "反查图片来源")
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
        items(items, key = { it.id }) { entry -> CachedRow(entry) }
    }
}

/**
 * 一条缓存内容。「最近看过」和「离线结果」共用 ——
 * 两处各写一套的话，改了一处忘了另一处，界面就会出现两种长得不一样的
 * "同一种东西"，而那种不一致没人会当成 bug 报上来，只是显得糙。
 */
@Composable
private fun CachedRow(entry: CachedItemEntity) {
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
