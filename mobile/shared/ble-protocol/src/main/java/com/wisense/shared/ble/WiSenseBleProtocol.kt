package com.wisense.shared.ble

import java.util.UUID

/**
 * BLE contract with the Wi-Sense RX board (ESP32-S3), mirroring
 * docs/app_architecture.md §3.1 exactly. The phone is a consumer of this
 * protocol — do not change values here without a firmware change.
 */
object WiSenseBleProtocol {

    /** Advertised device name of the ESP32-S3 classifier board. */
    const val DEVICE_NAME: String = "WiSense-RX"

    /** Primary GATT service exposed by the RX board. */
    val SERVICE_UUID: UUID = UUID.fromString("f19e0100-6a2c-418d-9e4a-2f5bc3e09a01")

    /** Single trigger characteristic: READ + NOTIFY, 1 byte. Never written to. */
    val TRIGGER_CHARACTERISTIC_UUID: UUID =
        UUID.fromString("f19e0200-6a2c-418d-9e4a-2f5bc3e09a01")

    /** Standard Client Characteristic Configuration Descriptor (subscribe/unsubscribe). */
    val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    /** One-byte trigger values sent by the RX board via notifications. */
    enum class Trigger(val code: Int) {
        /** 0x00 — board idle. */
        IDLE(0x00),

        /** 0x01 — fall confirmed (15s countdown expired, no button press). */
        ALERT(0x01),

        /** 0x02 — physical cancel button pressed after ALERT fired. */
        CANCEL(0x02);

        companion object {
            fun fromCode(code: Int): Trigger? = entries.firstOrNull { it.code == code }
        }
    }

    /**
     * Parse a raw notification payload into a [Trigger]. The characteristic
     * is exactly 1 byte; anything else is protocol noise and returns null.
     */
    fun parseTrigger(value: ByteArray?): Trigger? {
        if (value == null || value.size != 1) return null
        return Trigger.fromCode(value[0].toInt() and 0xFF)
    }
}
