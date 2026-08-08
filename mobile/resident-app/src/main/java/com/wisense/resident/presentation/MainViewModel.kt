package com.wisense.resident.presentation

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.wisense.resident.data.ble.BleMonitorService
import com.wisense.resident.data.ble.BleRepository
import com.wisense.resident.data.emergency.EmergencyStreamController
import com.wisense.resident.data.emergency.EmergencyStreamState
import com.wisense.resident.data.house.HouseRepository
import com.wisense.resident.data.settings.SettingsStore
import com.wisense.resident.domain.model.BleEvent
import com.wisense.resident.domain.model.ConnectionState
import com.wisense.shared.firebase.AuthClient
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.scan
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Backs both Phase-1 screens. Setup screen drives permissions + service
 * start; the Idle screen renders [connectionState] + the rolling [eventLog].
 */
@HiltViewModel
class MainViewModel @Inject constructor(
    private val bleRepository: BleRepository,
    private val settingsStore: SettingsStore,
    private val emergencyStreamController: EmergencyStreamController,
    private val houseRepository: HouseRepository,
    @ApplicationContext private val context: Context,
) : ViewModel() {

    val connectionState: StateFlow<ConnectionState> = bleRepository.connectionState

    /** Active emergency session (inactive when idle) — drives the Emergency screen. */
    val emergencyState: StateFlow<EmergencyStreamState> = emergencyStreamController.state

    /** The local camera track the Active Emergency screen renders. */
    val localVideoTrack = emergencyStreamController.localVideoTrack
    val eglBaseContext get() = emergencyStreamController.eglBaseContext

    /** Rolling log, newest first, capped so a long-running session stays bounded. */
    val eventLog: StateFlow<List<BleEvent>> = bleRepository.events
        .scan(emptyList<BleEvent>()) { acc, event ->
            (listOf(event) + acc).take(MAX_LOG_ENTRIES)
        }
        .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    /** Permissions the current OS version needs before monitoring can start. */
    val missingPermissions: StateFlow<List<String>> = MutableStateFlow(requiredPermissions())
        .asStateFlow()

    fun refreshPermissions() {
        (missingPermissions as MutableStateFlow).value = requiredPermissions()
    }

    /** Setup screen finished: permissions granted → start the monitor service. */
    fun startMonitoring() {
        BleMonitorService.start(context)
    }

    fun retryNow() = bleRepository.retryNow()

    /** Settings → re-pair: forget current device and rescan from scratch. */
    fun rePair() = bleRepository.rePair()

    // ---------------------------------------------------------------- Settings

    val showMonitorNotification: StateFlow<Boolean> = settingsStore.showMonitorNotification

    fun setShowMonitorNotification(show: Boolean) {
        settingsStore.setShowMonitorNotification(show)
    }

    // ---------------------------------------------------------------- Caregivers

    val houseId: StateFlow<String?> = settingsStore.houseId

    private val _addCaregiverResult = MutableStateFlow<String?>(null)
    val addCaregiverResult: StateFlow<String?> = _addCaregiverResult.asStateFlow()

    fun addCaregiver(caregiverCode: String) {
        viewModelScope.launch {
            _addCaregiverResult.value = try {
                houseRepository.addCaregiverByCode(caregiverCode)
                "Added."
            } catch (e: Exception) {
                "Failed: ${e.message ?: e}"
            }
        }
    }

    fun clearAddCaregiverResult() {
        _addCaregiverResult.value = null
    }

    /** Doesn't stop the monitor service — BLE monitoring is device-level, not
     * tied to who's signed in. Clears the cached house so a different
     * account signing in next doesn't inherit this one's house. */
    fun logout() {
        AuthClient.signOut()
        settingsStore.clearHouseId()
    }

    fun isPaired(): Boolean =
        connectionState.value is ConnectionState.Connected ||
            connectionState.value is ConnectionState.Connecting ||
            connectionState.value is ConnectionState.Retrying

    /** BLE + notifications: monitoring can't start without these. */
    private fun requiredPermissions(): List<String> = buildList {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            add(Manifest.permission.BLUETOOTH_SCAN)
            add(Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            add(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            add(Manifest.permission.POST_NOTIFICATIONS)
        }
    }.filter {
        ContextCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
    }

    /** Camera + mic: requested up front so an emergency never needs the app
     * foregrounded. Optional per §7 — denied just means cameraAvailable=false. */
    fun optionalCapturePermissions(): List<String> = buildList {
        add(Manifest.permission.CAMERA)
        add(Manifest.permission.RECORD_AUDIO)
    }.filter {
        ContextCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
    }

    /** All permissions Setup asks for in one batch (required + optional). */
    fun allSetupPermissions(): List<String> =
        requiredPermissions() + optionalCapturePermissions()

    companion object {
        private const val MAX_LOG_ENTRIES = 100
    }
}
