package com.synorive.mobile.ui.pairing

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.synorive.mobile.data.datastore.PairingSettings
import com.synorive.mobile.data.datastore.PairingState
import com.synorive.mobile.data.model.HealthResponse
import com.synorive.mobile.data.network.PairingProber
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

sealed interface ProbeStatus {
    data object Idle : ProbeStatus
    data object Probing : ProbeStatus
    data class Ok(val health: HealthResponse) : ProbeStatus
    data class Failed(val message: String) : ProbeStatus
}

class PairingViewModel(private val settings: PairingSettings) : ViewModel() {

    val saved: StateFlow<PairingState> = settings.state.stateIn(
        viewModelScope,
        SharingStarted.WhileSubscribed(5_000),
        PairingState(),
    )

    private val _probeStatus = MutableStateFlow<ProbeStatus>(ProbeStatus.Idle)
    val probeStatus: StateFlow<ProbeStatus> = _probeStatus

    /** "测试连接"：只探活，不保存——先确认地址/令牌是对的，用户点"保存"才落盘。 */
    fun probe(host: String, port: Int) {
        _probeStatus.value = ProbeStatus.Probing
        viewModelScope.launch {
            PairingProber.probe(host, port).fold(
                onSuccess = { _probeStatus.value = ProbeStatus.Ok(it) },
                onFailure = { _probeStatus.value = ProbeStatus.Failed(it.message ?: "连不上") },
            )
        }
    }

    fun save(host: String, port: Int, token: String) {
        viewModelScope.launch {
            settings.save(host, port, token)
        }
    }

    fun forget() {
        viewModelScope.launch {
            settings.clear()
            _probeStatus.value = ProbeStatus.Idle
        }
    }
}
