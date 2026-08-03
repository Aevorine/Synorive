package com.synorive.mobile.ui.camera

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.viewmodel.compose.viewModel as composeViewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.synorive.mobile.LocalAppContainer
import com.synorive.mobile.data.model.ReverseImageHit
import com.synorive.mobile.ui.reverse.reverseImageResultItems
import java.io.File

private const val FILE_PROVIDER_AUTHORITY = "com.synorive.mobile.fileprovider"

/** 8.7/W5：拍照反查——不是搜本地库，是问桌面引擎"这张图在网上哪还出现过"。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CameraSearchScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val container = LocalAppContainer.current
    val viewModel: CameraSearchViewModel = composeViewModel(
        factory = viewModelFactory { initializer { CameraSearchViewModel(container.engineRepository) } },
    )
    val uiState by viewModel.uiState.collectAsState()

    var pendingUri by remember { mutableStateOf<Uri?>(null) }

    val takePicture = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { success ->
        val uri = pendingUri
        if (success && uri != null) {
            viewModel.reverseSearch(context, uri)
        }
    }

    fun launchCamera() {
        val uri = createCaptureUri(context)
        pendingUri = uri
        takePicture.launch(uri)
    }

    val requestPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) launchCamera()
    }

    LaunchedEffect(Unit) {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) launchCamera() else requestPermission.launch(Manifest.permission.CAMERA)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("拍照反查（W5）") },
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
                is ReverseImageUiState.Idle -> Text("正在打开相机…")
                is ReverseImageUiState.Loading -> CircularProgressIndicator()
                is ReverseImageUiState.Failed -> Text(state.message, color = MaterialTheme.colorScheme.error)
                is ReverseImageUiState.Ok -> {
                    val result = state.result
                    if (result.pagesIncluding.isEmpty() && result.visualSimilar.isEmpty()) {
                        Text("网上没查到相关结果")
                    } else {
                        LazyColumn(
                            modifier = Modifier.fillMaxSize(),
                            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                        ) {
                            reverseImageResultItems(result)
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ReverseHitRow(hit: ReverseImageHit) {
    val context = LocalContext.current
    Column(
        Modifier
            .fillMaxWidth()
            .clickable(enabled = hit.pageUrl.isNotBlank()) {
                runCatching {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(hit.pageUrl)))
                }
            }
            .padding(vertical = 10.dp),
    ) {
        Text(hit.title.ifBlank { hit.pageUrl }, style = MaterialTheme.typography.titleMedium)
        if (hit.pageUrl.isNotBlank()) {
            Text(
                hit.pageUrl,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
            )
        }
    }
}

private fun createCaptureUri(context: Context): Uri {
    val dir = File(context.cacheDir, "camera").apply { mkdirs() }
    val file = File(dir, "capture_${System.currentTimeMillis()}.jpg")
    return FileProvider.getUriForFile(context, FILE_PROVIDER_AUTHORITY, file)
}
