package com.wisense.resident.data.ble

import com.wisense.resident.domain.model.BleEvent
import com.wisense.resident.domain.model.ConnectionState
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class BleRepositoryImpl @Inject constructor(
    private val manager: BleConnectionManager,
) : BleRepository {

    override val connectionState: StateFlow<ConnectionState> = manager.connectionState
    override val events: SharedFlow<BleEvent> = manager.events

    override fun startMonitoring() = manager.startMonitoring()
    override fun stopMonitoring() = manager.stopMonitoring()
    override fun retryNow() = manager.retryNow()
    override fun rePair() = manager.rePair()
}
