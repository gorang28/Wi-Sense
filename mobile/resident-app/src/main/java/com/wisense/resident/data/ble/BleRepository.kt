package com.wisense.resident.data.ble

import com.wisense.resident.domain.model.BleEvent
import com.wisense.resident.domain.model.ConnectionState
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

/** UI-facing surface over the BLE link. Owned by [BleConnectionManager]. */
interface BleRepository {
    val connectionState: StateFlow<ConnectionState>
    val events: SharedFlow<BleEvent>

    fun startMonitoring()
    fun stopMonitoring()
    fun retryNow()

    /**
     * Forget the current device and re-run the full scan→connect→subscribe
     * sequence. Used by Settings → "Re-pair device". The connection itself
     * is open GATT (no bonding), so re-pairing just means reconnecting.
     */
    fun rePair()
}
