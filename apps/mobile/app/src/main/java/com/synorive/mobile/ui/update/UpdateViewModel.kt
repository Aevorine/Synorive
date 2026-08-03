package com.synorive.mobile.ui.update

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.synorive.mobile.data.update.ApkInstaller
import com.synorive.mobile.data.update.UpdateCheck
import com.synorive.mobile.data.update.UpdateRepository
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.launch
import java.io.File

/**
 * U 组 · 安卓端更新的界面状态机
 *
 * 六个状态一个都不能省，因为它们对应六种**用户该做的不同的事**：
 * 什么都不用做 / 等一会 / 点下载 / 等下载 / 点安装 / 去处理错误。
 * 合并任意两个，界面就会在某种情况下给出错误的指引。
 */
sealed interface UpdateUiState {
    data object Idle : UpdateUiState
    data object Checking : UpdateUiState
    data object UpToDate : UpdateUiState
    data class Available(val info: UpdateCheck.Available) : UpdateUiState
    data class Downloading(
        val info: UpdateCheck.Available,
        val transferred: Long,
        val total: Long,
    ) : UpdateUiState

    data class Ready(val info: UpdateCheck.Available, val file: File) : UpdateUiState
    data class Problem(val message: String, val releaseUrl: String?) : UpdateUiState
}

class UpdateViewModel(app: Application) : AndroidViewModel(app) {

    private val repository = UpdateRepository()

    private val _state = MutableStateFlow<UpdateUiState>(UpdateUiState.Idle)
    val state: StateFlow<UpdateUiState> = _state.asStateFlow()

    val currentVersionName: String get() = repository.currentVersionName
    val releasesPageUrl: String get() = repository.releasesPageUrl

    private var downloadJob: Job? = null

    fun check() {
        if (_state.value is UpdateUiState.Checking) return
        // 已经下好了就别重查 —— 重查会把状态打回 Available，
        // 界面上那个「立即安装」按钮凭空变回「下载」，用户以为下载白费了
        if (_state.value is UpdateUiState.Ready) return

        _state.value = UpdateUiState.Checking
        viewModelScope.launch {
            _state.value = when (val r = repository.check()) {
                is UpdateCheck.UpToDate -> UpdateUiState.UpToDate
                is UpdateCheck.Available -> UpdateUiState.Available(r)
                is UpdateCheck.NoApkAsset -> UpdateUiState.Problem(
                    "有新版本 v${r.versionName}，但这次发布里没有安卓安装包。可以去发布页看看。",
                    r.releaseUrl,
                )
                is UpdateCheck.Failed -> UpdateUiState.Problem(r.message, null)
            }
        }
    }

    fun download() {
        val info = (_state.value as? UpdateUiState.Available)?.info ?: return
        val dir = ApkInstaller.downloadDir(getApplication())
        val name = "Synorive-${info.versionName}.apk"

        _state.value = UpdateUiState.Downloading(info, 0, info.apkSize)
        downloadJob?.cancel()
        downloadJob = viewModelScope.launch {
            // 把 GitHub 报的资产大小传下去 —— 下载器靠它判断"是真下完了
            // 还是服务端提前断了连接"。少了它，截断的包会被当成好包拿去装
            repository.download(info.apkUrl, dir, name, info.apkSize)
                .catch { e ->
                    _state.value = UpdateUiState.Problem(
                        "下载失败：${e.message ?: "网络中断"}。可以重试，或去发布页手动下载。",
                        info.releaseUrl,
                    )
                }
                .collect { p ->
                    val done = p.total > 0 && p.transferred >= p.total
                    _state.value = if (done) {
                        UpdateUiState.Ready(info, File(dir, name))
                    } else {
                        UpdateUiState.Downloading(info, p.transferred, p.total)
                    }
                }
        }
    }

    /** @return null = 安装器已拉起；非 null = 给用户看的原因 */
    fun install(): String? {
        val ready = _state.value as? UpdateUiState.Ready ?: return "还没有下载好的安装包。"
        return ApkInstaller.install(getApplication(), ready.file)
    }

    fun needsInstallPermission(): Boolean = !ApkInstaller.canRequestInstall(getApplication())

    fun openInstallPermissionSettings() =
        ApkInstaller.openInstallPermissionSettings(getApplication())

    fun dismissProblem() {
        _state.value = UpdateUiState.Idle
    }

    override fun onCleared() {
        downloadJob?.cancel()
        super.onCleared()
    }
}
