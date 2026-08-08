package com.wisense.resident.domain.model

/**
 * Observable state of the resident phone's link to the house's RX board.
 */
sealed interface ConnectionState {

    data object Idle : ConnectionState

    /** Actively scanning for the advertised "WiSense-RX" device. */
    data object Scanning : ConnectionState

    /** Found it — GATT connect + service discovery in progress. */
    data class Connecting(val deviceAddress: String) : ConnectionState

    /** Connected and subscribed to the trigger characteristic. */
    data class Connected(
        val deviceAddress: String,
        val deviceName: String?,
    ) : ConnectionState

    /** Link dropped; reconnect attempts follow an exponential backoff. */
    data class Retrying(
        val deviceAddress: String?,
        val attempt: Int,
        val nextRetryInSeconds: Int,
    ) : ConnectionState

    /** Retries exhausted — manual intervention required (§7 "give up loudly"). */
    data class GiveUp(val deviceAddress: String?) : ConnectionState

    /** Bluetooth off, permissions missing, or adapter unavailable. */
    data class Unavailable(val reason: String) : ConnectionState
}

/** One entry in the on-screen event log (§3.1 trigger values + link events). */
data class BleEvent(
    val timestampMillis: Long,
    val kind: Kind,
    val detail: String,
) {
    enum class Kind { ALERT, CANCEL, IDLE, CONNECTED, DISCONNECTED, ERROR }
}
