package com.wisense.resident.data.ble

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.content.ContextCompat
import com.wisense.resident.domain.model.BleEvent
import com.wisense.resident.domain.model.ConnectionState
import com.wisense.shared.ble.WiSenseBleProtocol
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Owns the entire BLE lifecycle with the house's RX board:
 * scan → connect → subscribe → hold the link open → reconnect on loss.
 *
 * This manager is what the foreground service keeps alive. It exposes
 * [connectionState] (what the UI renders) and [events] (the on-screen log).
 * Reconnects use exponential backoff, capped, then give up loudly per §7.
 */
class BleConnectionManager(
    private val context: Context,
    private val bluetoothManager: BluetoothManager,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val connectMutex = Mutex()

    private val _connectionState = MutableStateFlow<ConnectionState>(ConnectionState.Idle)
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    private val _events = MutableSharedFlow<BleEvent>(extraBufferCapacity = EVENT_BUFFER)
    val events: SharedFlow<BleEvent> = _events.asSharedFlow()

    private var gatt: BluetoothGatt? = null
    private var scanJob: Job? = null
    private var reconnectJob: Job? = null
    private var retryCount = 0
    private var lastAddress: String? = null

    private val adapter get() = bluetoothManager.adapter
    private val scanner get() = adapter?.bluetoothLeScanner

    /** True while the service wants the link held — gates the reconnect loop. */
    @Volatile
    private var monitoring = false

    // ------------------------------------------------------------------ API

    fun startMonitoring() {
        if (monitoring) return
        monitoring = true
        retryCount = 0

        when {
            adapter == null -> {
                _connectionState.value = ConnectionState.Unavailable("Bluetooth not supported on this device")
            }
            !adapter!!.isEnabled -> {
                _connectionState.value = ConnectionState.Unavailable("Bluetooth is off — turn it on to monitor")
            }
            !hasConnectPermission() -> {
                _connectionState.value = ConnectionState.Unavailable("Bluetooth permission not granted")
            }
            else -> startScan()
        }
    }

    fun stopMonitoring() {
        monitoring = false
        scanJob?.cancel()
        reconnectJob?.cancel()
        closeGatt()
        _connectionState.value = ConnectionState.Idle
    }

    /** Manual retry from the GiveUp / Unavailable states. */
    fun retryNow() {
        if (!monitoring) return
        retryCount = 0
        startScan()
    }

    /**
     * Forget the current device and re-run the full scan→connect→subscribe
     * sequence (Settings → "Re-pair device"). Open GATT means re-pairing is
     * just a clean reconnect; this resets backoff and drops any stale GATT.
     */
    fun rePair() {
        if (!monitoring) {
            startMonitoring()
            return
        }
        retryCount = 0
        lastAddress = null
        reconnectJob?.cancel()
        scanJob?.cancel()
        closeGatt()
        emit(BleEvent.Kind.IDLE, "re-pairing — scanning for sensor")
        startScan()
    }

    // ------------------------------------------------------------ Scanning

    private fun startScan() {
        val leScanner = scanner ?: run {
            _connectionState.value = ConnectionState.Unavailable("Bluetooth is off — turn it on to monitor")
            return
        }
        if (!hasScanPermission()) {
            _connectionState.value = ConnectionState.Unavailable("Bluetooth scan permission not granted")
            return
        }

        scanJob?.cancel()
        closeGatt()
        // A scan may already be in flight (e.g. retryNow()/rePair() called while
        // Scanning) — stopping the coroutine timeout above doesn't stop the OS-level
        // scan session, and starting a second one fails with SCAN_FAILED_ALREADY_STARTED.
        stopScanQuietly(leScanner)
        _connectionState.value = ConnectionState.Scanning

        // No OS-level ScanFilter: the RX board's service UUID isn't exposed via a
        // Service-UUID-list AD field Android's ScanFilter can match (confirmed on
        // real hardware — the OS scanner parses serviceUuids=null for its adverts,
        // even though it's visible in the raw payload). Matching is done by device
        // name instead, case-insensitively, in onScanResult below.
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()

        @SuppressLint("MissingPermission")
        leScanner.startScan(emptyList(), settings, scanCallback)
        Log.d(TAG, "scan started (name match, no UUID filter)")

        scanJob = scope.launch {
            delay(SCAN_TIMEOUT_MS)
            // Device not found — fall into the same retry loop as a drop.
            @SuppressLint("MissingPermission")
            leScanner.stopScan(scanCallback)
            scheduleReconnect(reason = "scan timeout")
        }
    }

    @SuppressLint("MissingPermission")
    private fun stopScanQuietly(leScanner: android.bluetooth.le.BluetoothLeScanner) {
        try {
            leScanner.stopScan(scanCallback)
        } catch (e: Exception) {
            // No scan was active — expected on the common path, not an error.
            Log.d(TAG, "stopScan before restart: ${e.message}")
        }
    }

    private val scanCallback = object : ScanCallback() {
        @SuppressLint("MissingPermission")
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val device = result.device ?: return
            val name = result.scanRecord?.deviceName ?: device.name
            // Case-insensitive: guards against firmware casing drift now that this
            // is the only match criterion (no OS-level UUID filter backing it up).
            if (!name.equals(WiSenseBleProtocol.DEVICE_NAME, ignoreCase = true)) return

            Log.d(TAG, "found $name at ${device.address}")
            scanner?.stopScan(this)
            scanJob?.cancel()
            connect(device)
        }

        @SuppressLint("MissingPermission")
        override fun onScanFailed(errorCode: Int) {
            Log.w(TAG, "scan failed: $errorCode")
            scanner?.stopScan(this)
            scanJob?.cancel()
            scheduleReconnect(reason = "scan failed $errorCode")
        }
    }

    // ------------------------------------------------------------ Connect

    @SuppressLint("MissingPermission")
    private fun connect(device: BluetoothDevice) {
        lastAddress = device.address
        _connectionState.value = ConnectionState.Connecting(device.address)
        gatt?.close()
        // autoConnect=false for the initial connect: faster, and the retry
        // loop handles drops with explicit backoff anyway.
        gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
    }

    private val gattCallback = object : BluetoothGattCallback() {

        @SuppressLint("MissingPermission")
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    Log.d(TAG, "GATT connected (status=$status), discovering services")
                    g.discoverServices()
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    Log.d(TAG, "GATT disconnected (status=$status)")
                    emit(BleEvent.Kind.DISCONNECTED, "link lost (gatt status $status)")
                    closeGatt()
                    scheduleReconnect(reason = "disconnected")
                }
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.w(TAG, "service discovery failed: $status")
                closeGatt()
                scheduleReconnect(reason = "service discovery failed")
                return
            }
            val service: BluetoothGattService? = g.getService(WiSenseBleProtocol.SERVICE_UUID)
            val chr = service?.getCharacteristic(WiSenseBleProtocol.TRIGGER_CHARACTERISTIC_UUID)
            if (chr == null) {
                Log.w(TAG, "trigger characteristic not found — wrong device?")
                emit(BleEvent.Kind.ERROR, "trigger characteristic missing")
                closeGatt()
                scheduleReconnect(reason = "characteristic missing")
                return
            }
            subscribe(g, chr)
        }

        @SuppressLint("MissingPermission")
        private fun subscribe(g: BluetoothGatt, chr: BluetoothGattCharacteristic) {
            if (!g.setCharacteristicNotification(chr, true)) {
                Log.w(TAG, "setCharacteristicNotification failed")
                closeGatt()
                scheduleReconnect(reason = "local subscribe failed")
                return
            }
            val cccd = chr.getDescriptor(WiSenseBleProtocol.CCCD_UUID)
            if (cccd == null) {
                Log.w(TAG, "CCCD missing")
                closeGatt()
                scheduleReconnect(reason = "CCCD missing")
                return
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                g.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
            } else {
                @Suppress("DEPRECATION")
                cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                @Suppress("DEPRECATION")
                g.writeDescriptor(cccd)
            }
        }

        @SuppressLint("MissingPermission")
        override fun onDescriptorWrite(g: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            if (descriptor.uuid == WiSenseBleProtocol.CCCD_UUID && status == BluetoothGatt.GATT_SUCCESS) {
                retryCount = 0
                val device = g.device
                val name = device.name ?: WiSenseBleProtocol.DEVICE_NAME
                _connectionState.value = ConnectionState.Connected(device.address, name)
                emit(BleEvent.Kind.CONNECTED, "subscribed to $name (${device.address})")
                Log.d(TAG, "subscribed — link held open")

                // Read the current value so the UI reflects state immediately.
                g.getService(WiSenseBleProtocol.SERVICE_UUID)
                    ?.getCharacteristic(WiSenseBleProtocol.TRIGGER_CHARACTERISTIC_UUID)
                    ?.let { g.readCharacteristic(it) }
            } else if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.w(TAG, "CCCD write failed: $status")
                closeGatt()
                scheduleReconnect(reason = "subscribe failed")
            }
        }

        @Deprecated("pre-33 callback")
        override fun onCharacteristicChanged(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
        ) {
            @Suppress("DEPRECATION")
            handleTrigger(characteristic.value)
        }

        override fun onCharacteristicChanged(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
        ) {
            handleTrigger(value)
        }

        @Deprecated("pre-33 callback")
        override fun onCharacteristicRead(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int,
        ) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                @Suppress("DEPRECATION")
                handleTrigger(characteristic.value)
            }
        }

        override fun onCharacteristicRead(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
            status: Int,
        ) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                handleTrigger(value)
            }
        }
    }

    private fun handleTrigger(value: ByteArray?) {
        val trigger = WiSenseBleProtocol.parseTrigger(value) ?: return
        when (trigger) {
            WiSenseBleProtocol.Trigger.ALERT -> emit(BleEvent.Kind.ALERT, "ALERT received (0x01) — fall confirmed")
            WiSenseBleProtocol.Trigger.CANCEL -> emit(BleEvent.Kind.CANCEL, "CANCEL received (0x02) — cancelled at device")
            WiSenseBleProtocol.Trigger.IDLE -> emit(BleEvent.Kind.IDLE, "device reports idle")
        }
        Log.d(TAG, "trigger: $trigger")
    }

    // ------------------------------------------------------------ Reconnect

    private fun scheduleReconnect(reason: String) {
        if (!monitoring) return
        if (reconnectJob?.isActive == true) return

        retryCount++
        if (retryCount > MAX_RETRIES) {
            _connectionState.value = ConnectionState.GiveUp(lastAddress)
            emit(BleEvent.Kind.ERROR, "gave up after $MAX_RETRIES retries ($reason)")
            return
        }

        val backoffSeconds = (INITIAL_BACKOFF_S * (1 shl (retryCount - 1).coerceAtMost(5)))
            .coerceAtMost(MAX_BACKOFF_S)
        _connectionState.value = ConnectionState.Retrying(lastAddress, retryCount, backoffSeconds)

        reconnectJob = scope.launch {
            delay(backoffSeconds * 1000L)
            if (monitoring) startScan()
        }
    }

    @SuppressLint("MissingPermission")
    private fun closeGatt() {
        gatt?.disconnect()
        gatt?.close()
        gatt = null
    }

    // ------------------------------------------------------------ Helpers

    private fun emit(kind: BleEvent.Kind, detail: String) {
        scope.launch {
            _events.emit(BleEvent(System.currentTimeMillis(), kind, detail))
        }
    }

    private fun hasScanPermission(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            ContextCompat.checkSelfPermission(context, Manifest.permission.BLUETOOTH_SCAN) ==
                PackageManager.PERMISSION_GRANTED
        } else {
            ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED
        }

    private fun hasConnectPermission(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            ContextCompat.checkSelfPermission(context, Manifest.permission.BLUETOOTH_CONNECT) ==
                PackageManager.PERMISSION_GRANTED
        } else true

    companion object {
        private const val TAG = "BleConnectionManager"
        private const val EVENT_BUFFER = 64
        private const val SCAN_TIMEOUT_MS = 20_000L
        private const val MAX_RETRIES = 10
        private const val INITIAL_BACKOFF_S = 2
        private const val MAX_BACKOFF_S = 60
    }
}
