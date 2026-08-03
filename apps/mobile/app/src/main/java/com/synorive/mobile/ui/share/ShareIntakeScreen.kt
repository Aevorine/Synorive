package com.synorive.mobile.ui.share

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel as composeViewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import coil.compose.AsyncImage
import com.synorive.mobile.LocalAppContainer
import com.synorive.mobile.ui.camera.ReverseHitRow

/**
 * 系统分享菜单（或未来的相机胶卷分享）把内容递进来时打开的页面。
 * 两件事互相独立：「存入资料库」适用于任何内容；「反查图片来源」只对图片有意义，
 * 是 8.7/W5 在分享场景下的入口。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ShareIntakeScreen(content: SharedContent, onDone: () -> Unit) {
    val context = LocalContext.current
    val container = LocalAppContainer.current
    val viewModel: ShareIntakeViewModel = composeViewModel(
        factory = viewModelFactory { initializer { ShareIntakeViewModel(container.engineRepository) } },
    )

    val ingestState by viewModel.ingestState.collectAsState()
    val reverseOpState by viewModel.reverseOpState.collectAsState()
    val reverseResult by viewModel.reverseResult.collectAsState()

    val mimeType = content.mimeType.orEmpty()
    val isImage = mimeType.startsWith("image/")
    val isVideo = mimeType.startsWith("video/")

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("投喂") },
                navigationIcon = {
                    IconButton(onClick = onDone) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(20.dp),
        ) {
            when {
                content.text != null -> {
                    Text("分享的文本/链接", style = MaterialTheme.typography.titleMedium)
                    Text(
                        content.text,
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.padding(top = 8.dp, bottom = 16.dp),
                    )
                }
                content.uri != null && isImage -> {
                    AsyncImage(
                        model = content.uri,
                        contentDescription = null,
                        modifier = Modifier.fillMaxWidth().height(220.dp).padding(bottom = 16.dp),
                    )
                }
                content.uri != null && isVideo -> {
                    Text("分享了一段视频", style = MaterialTheme.typography.titleMedium)
                }
                else -> Text("不认识这种分享内容", color = MaterialTheme.colorScheme.error)
            }

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    onClick = {
                        val text = content.text
                        val uri = content.uri
                        if (text != null) viewModel.ingestText(text) else if (uri != null) viewModel.ingestUri(context, uri)
                    },
                    enabled = ingestState !is ShareOpState.Working,
                ) {
                    Text("存入资料库")
                }

                if (isImage && content.uri != null) {
                    OutlinedButton(
                        onClick = { viewModel.reverseImage(context, content.uri) },
                        enabled = reverseOpState !is ShareOpState.Working,
                    ) {
                        Text("反查图片来源（W5）")
                    }
                }
            }

            when (val s = ingestState) {
                is ShareOpState.Working -> CircularProgressIndicator(Modifier.padding(top = 14.dp))
                is ShareOpState.Done -> Text(
                    s.message,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(top = 14.dp),
                )
                is ShareOpState.Failed -> Text(
                    s.message,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(top = 14.dp),
                )
                is ShareOpState.Idle -> {}
            }

            if (isVideo) {
                Text(
                    "视频的「反查来源（W6）」要等它在资料库里处理完（提取关键帧）之后才能做——" +
                        "存进去，处理完成后到「搜索」结果里对它点反查图标即可。手机端目前没法" +
                        "知道处理进度，所以这一步没有直接放在这个页面里。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }

            when (val s = reverseOpState) {
                is ShareOpState.Working -> CircularProgressIndicator(Modifier.padding(top = 14.dp))
                is ShareOpState.Failed -> Text(
                    s.message,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(top = 14.dp),
                )
                else -> {}
            }

            // 这个 Column 本身已经在滚动了，结果直接铺开渲染而不是套一个 LazyColumn——
            // 可滚动容器里嵌套一个高度不设限的懒加载列表会在运行时崩（经典的
            // "measured with an infinity maximum height constraints"）。反查结果
            // 一般就几条到十几条，直接展开渲染没有性能问题。
            reverseResult?.let { result ->
                if (result.pagesIncluding.isEmpty() && result.visualSimilar.isEmpty()) {
                    Text("网上没查到相关结果", modifier = Modifier.padding(top = 8.dp))
                } else {
                    result.bestGuess?.let {
                        Text(
                            "看起来像：$it",
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.padding(top = 16.dp),
                        )
                    }
                    result.pagesIncluding.forEach { hit -> ReverseHitRow(hit) }
                    result.visualSimilar.forEach { hit -> ReverseHitRow(hit) }
                }
            }
        }
    }
}
