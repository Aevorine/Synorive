package com.synorive.mobile.ui.pairing

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel as composeViewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.synorive.mobile.LocalAppContainer
import com.synorive.mobile.ui.update.UpdateCard

/**
 * 配对设置：桌面端「设置 → 安卓配对」面板显示地址和令牌，这里手动填一遍。
 * 没走自动发现（mDNS/UDP 广播在不少路由器/公共 Wi-Fi 上会被挡），
 * "填一次、存起来、以后不用再填"换的是稳定性。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PairingScreen() {
    val container = LocalAppContainer.current
    val viewModel: PairingViewModel = composeViewModel(
        factory = viewModelFactory { initializer { PairingViewModel(container.pairingSettings) } },
    )

    val saved by viewModel.saved.collectAsState()
    val probeStatus by viewModel.probeStatus.collectAsState()

    var host by rememberSaveable(saved.host) { mutableStateOf(saved.host) }
    var portText by rememberSaveable(saved.port) { mutableStateOf(if (saved.port > 0) saved.port.toString() else "") }
    var token by rememberSaveable(saved.token) { mutableStateOf(saved.token) }

    val port = portText.toIntOrNull() ?: 0
    val canSubmit = host.isNotBlank() && port in 1..65535 && token.isNotBlank()

    Scaffold(
        topBar = { TopAppBar(title = { Text("安卓配对") }) },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(20.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text(
                "在电脑上打开桌面端「设置 → 安卓配对」，把里面显示的地址和配对令牌填在下面。" +
                    "两台设备要在同一个局域网（同一个 Wi-Fi）里才连得上。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            OutlinedTextField(
                value = host,
                onValueChange = { host = it },
                label = { Text("地址（IP）") },
                placeholder = { Text("例如 192.168.1.23") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            OutlinedTextField(
                value = portText,
                onValueChange = { portText = it.filter(Char::isDigit) },
                label = { Text("端口") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            OutlinedTextField(
                value = token,
                onValueChange = { token = it },
                label = { Text("配对令牌") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedButton(
                    onClick = { viewModel.probe(host.trim(), port) },
                    enabled = host.isNotBlank() && port in 1..65535,
                ) {
                    Text("测试连接")
                }

                when (val status = probeStatus) {
                    is ProbeStatus.Probing -> CircularProgressIndicator(modifier = Modifier.size(18.dp))
                    is ProbeStatus.Ok -> Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.CheckCircle, contentDescription = null, tint = Color(0xFF2FA86B))
                        Text(
                            " 连通了 · 库里 ${status.health.indexedItems} 条内容",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                    is ProbeStatus.Failed -> Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.Error, contentDescription = null, tint = MaterialTheme.colorScheme.error)
                        Text(" ${status.message}", style = MaterialTheme.typography.bodySmall)
                    }
                    is ProbeStatus.Idle -> {}
                }
            }

            Button(
                onClick = { viewModel.save(host.trim(), port, token.trim()) },
                enabled = canSubmit,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("保存配对")
            }

            if (saved.paired) {
                Row(
                    modifier = Modifier.fillMaxWidth().wrapContentSize(Alignment.CenterEnd),
                ) {
                    TextButton(onClick = { viewModel.forget() }) {
                        Text("忘记这台设备", color = MaterialTheme.colorScheme.error)
                    }
                }
            }

            // U 组 应用更新。挂在这一页底部而不是单开一个底部标签——
            // 这是全 App 唯一的"设置性质"页面，而更新是一个月看一次的东西，
            // 不该把两个常驻入口挤成三个。它**不依赖配对**，没连上电脑也能用。
            UpdateCard()
        }
    }
}
