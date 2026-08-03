package com.synorive.mobile.ui.update

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel

/**
 * U 组 · 「关于与更新」卡片
 *
 * 挂在配对页底部：那是这个 App 里唯一一个"设置性质"的页面，
 * 为更新单开一个底部标签会把两个常驻入口挤成三个，而更新是
 * 一个月看一次的东西，不配占一个常驻位。
 *
 * 🔴 「立即安装」之前必须先查安装权限。API 26+ 上没有这个权限时，
 *    startActivity 会被系统**静默**吞掉 —— 用户点了完全没反应，
 *    也没有任何日志，是这条链路上最难自查的一个坑。
 */
@Composable
fun UpdateCard(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val vm: UpdateViewModel = viewModel()
    val state by vm.state.collectAsState()
    var installError by remember { mutableStateOf<String?>(null) }
    val notesScroll = rememberScrollState()

    // 进页面自动查一次。只发一个"最新版本号是多少"的请求，不含任何用户内容
    LaunchedEffect(Unit) {
        if (state is UpdateUiState.Idle) vm.check()
    }

    val openUrl: (String) -> Unit = { url ->
        runCatching {
            context.startActivity(
                Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
        }
    }

    Card(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("关于与更新", style = MaterialTheme.typography.titleMedium)
            Text(
                "当前版本 v${vm.currentVersionName}",
                style = MaterialTheme.typography.bodyMedium,
            )

            when (val s = state) {
                is UpdateUiState.Idle -> Unit

                is UpdateUiState.Checking -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                    Text("  正在检查更新…", style = MaterialTheme.typography.bodySmall)
                }

                is UpdateUiState.UpToDate -> Text(
                    "已是最新版本。",
                    style = MaterialTheme.typography.bodySmall,
                )

                is UpdateUiState.Available -> {
                    Text(
                        "发现新版本 v${s.info.versionName}" +
                            if (s.info.apkSize > 0) "（${humanSize(s.info.apkSize)}）" else "",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    if (s.info.notes.isNotBlank()) {
                        // 更新说明可能很长（Release body 是自由文本），
                        // 给它一个高度上限并允许滚动，不让它把整张卡片撑到看不见按钮
                        Text(
                            s.info.notes,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier
                                .fillMaxWidth()
                                .heightIn(max = 160.dp)
                                .verticalScroll(notesScroll),
                        )
                    }
                    Button(onClick = { vm.download() }, modifier = Modifier.fillMaxWidth()) {
                        Text("下载并安装")
                    }
                }

                is UpdateUiState.Downloading -> {
                    val pct = if (s.total > 0) (s.transferred * 100 / s.total).toInt() else -1
                    if (pct >= 0) {
                        LinearProgressIndicator(
                            progress = { pct / 100f },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Text(
                            "正在下载 $pct% · ${humanSize(s.transferred)} / ${humanSize(s.total)}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    } else {
                        LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                        Text(
                            "正在下载 ${humanSize(s.transferred)}（服务端没给总大小）",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }

                is UpdateUiState.Ready -> {
                    Text(
                        "v${s.info.versionName} 已下载完成。点下面按钮会由系统弹出安装确认。",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    if (vm.needsInstallPermission()) {
                        Text(
                            "系统还没允许 Synorive 安装应用——先打开这个开关，回来再点安装。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                        OutlinedButton(
                            onClick = { vm.openInstallPermissionSettings() },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text("去打开「允许安装未知应用」")
                        }
                    }
                    Button(
                        onClick = { installError = vm.install() },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("立即安装")
                    }
                    installError?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    }
                }

                is UpdateUiState.Problem -> {
                    Text(
                        s.message,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                    s.releaseUrl?.let { url ->
                        TextButton(onClick = { openUrl(url) }) { Text("打开发布页") }
                    }
                }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = {
                        installError = null
                        vm.dismissProblem()
                        vm.check()
                    },
                    enabled = state !is UpdateUiState.Checking && state !is UpdateUiState.Downloading,
                ) {
                    Text("检查更新")
                }
                TextButton(onClick = { openUrl(vm.releasesPageUrl) }) { Text("发布页") }
            }
        }
    }
}

private fun humanSize(bytes: Long): String {
    if (bytes <= 0) return "0 MB"
    val mb = bytes / 1024.0 / 1024.0
    return if (mb >= 1024) String.format("%.2f GB", mb / 1024) else String.format("%.1f MB", mb)
}
