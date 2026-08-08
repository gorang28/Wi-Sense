package com.wisense.resident.data.ble

import android.app.Notification
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.lifecycle.LifecycleService
import com.wisense.resident.MainActivity
import com.wisense.resident.R
import com.wisense.resident.WiSenseResidentApp
import com.wisense.resident.data.emergency.EmergencyStreamController
import com.wisense.resident.data.house.HouseRepository
import com.wisense.resident.data.settings.SettingsStore
import com.wisense.resident.domain.model.BleEvent
import com.wisense.resident.domain.model.ConnectionState
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import javax.inject.Inject

/**
 * Foreground service that holds the BLE link to the RX board with the
 * screen off — the mechanism Android requires for reliable background BLE.
 *
 * On ALERT it escalates the foreground-service type to camera+microphone
 * (required since Android 14) and starts capture+streaming via
 * [EmergencyStreamController]; on CANCEL it stops and drops back to the
 * connectedDevice type.
 */
@AndroidEntryPoint
class BleMonitorService : LifecycleService() {

    @Inject
    lateinit var bleRepository: BleRepository

    @Inject
    lateinit var settingsStore: SettingsStore

    @Inject
    lateinit var emergencyStreamController: EmergencyStreamController

    @Inject
    lateinit var houseRepository: HouseRepository

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onBind(intent: Intent): IBinder? {
        super.onBind(intent)
        return null
    }

    override fun onCreate() {
        super.onCreate()
        startForegroundWithNotification(
            getString(R.string.monitor_notification_starting),
            inEmergency = false,
        )
        bleRepository.connectionState
            .onEach { state ->
                updateNotification(state.notificationText())
                if (state is ConnectionState.Connected) {
                    // Best-effort: a Firestore write failing here shouldn't
                    // affect the BLE link itself, so it's non-fatal — no
                    // house yet is also handled (no-ops) inside the call.
                    runCatching { houseRepository.setResidentDeviceBleId(state.deviceAddress) }
                        .onSuccess { Log.d(TAG, "recorded resident device BLE id ${state.deviceAddress}") }
                        .onFailure { Log.w(TAG, "failed to record resident device BLE id", it) }
                }
            }
            .launchIn(scope)
        bleRepository.events
            .onEach { event -> handleTriggerEvent(event) }
            .launchIn(scope)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_STOP -> stopSelf()
            else -> bleRepository.startMonitoring()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        emergencyStreamController.stop()
        bleRepository.stopMonitoring()
        scope.cancel()
        super.onDestroy()
    }

    // ------------------------------------------------------------ Emergency

    private fun handleTriggerEvent(event: BleEvent) {
        when (event.kind) {
            BleEvent.Kind.ALERT -> startEmergency()
            BleEvent.Kind.CANCEL -> stopEmergency()
            else -> Unit
        }
    }

    private fun startEmergency() {
        if (emergencyStreamController.active) return
        // Escalate foreground-service type to camera+mic BEFORE using them —
        // required for a backgrounded app since Android 14.
        startForegroundWithNotification(
            getString(R.string.monitor_notification_emergency),
            inEmergency = true,
        )
        emergencyStreamController.start()
    }

    private fun stopEmergency() {
        if (!emergencyStreamController.active) return
        emergencyStreamController.stop()
        // Drop back to the lightweight connectedDevice type.
        startForegroundWithNotification(
            bleRepository.connectionState.value.notificationText(),
            inEmergency = false,
        )
    }

    // ------------------------------------------------------------ Notification

    private fun startForegroundWithNotification(text: String, inEmergency: Boolean) {
        val notification = buildNotification(text)
        val serviceType = foregroundServiceType(inEmergency)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, serviceType)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        if (!settingsStore.showMonitorNotification.value) {
            stopForeground(STOP_FOREGROUND_DETACH)
        }
    }

    private fun foregroundServiceType(inEmergency: Boolean): Int {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return 0
        var type = ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
        if (inEmergency && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            type = type or
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA or
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        }
        return type
    }

    private fun updateNotification(text: String) {
        if (!settingsStore.showMonitorNotification.value) return
        if (emergencyStreamController.active) return // emergency text owns it
        val manager = getSystemService(NOTIFICATION_SERVICE) as android.app.NotificationManager
        manager.notify(NOTIFICATION_ID, buildNotification(text))
    }

    private fun buildNotification(text: String): Notification {
        val openApp = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, WiSenseResidentApp.MONITOR_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setOngoing(true)
            .setContentIntent(openApp)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    private fun ConnectionState.notificationText(): String = when (this) {
        is ConnectionState.Idle -> getString(R.string.monitor_notification_starting)
        is ConnectionState.Scanning -> getString(R.string.monitor_notification_scanning)
        is ConnectionState.Connecting -> getString(R.string.monitor_notification_connecting)
        is ConnectionState.Connected -> getString(R.string.monitor_notification_connected)
        is ConnectionState.Retrying ->
            getString(R.string.monitor_notification_retrying, nextRetryInSeconds)
        is ConnectionState.GiveUp -> getString(R.string.monitor_notification_give_up)
        is ConnectionState.Unavailable -> reason
    }

    companion object {
        private const val TAG = "BleMonitorService"
        private const val NOTIFICATION_ID = 1001
        const val ACTION_START = "com.wisense.resident.action.START_MONITOR"
        const val ACTION_STOP = "com.wisense.resident.action.STOP_MONITOR"

        fun start(context: Context) {
            val intent = Intent(context, BleMonitorService::class.java).setAction(ACTION_START)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.startService(
                Intent(context, BleMonitorService::class.java).setAction(ACTION_STOP),
            )
        }
    }
}
