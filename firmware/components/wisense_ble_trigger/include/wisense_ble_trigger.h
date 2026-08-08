/*
 * WiSense BLE emergency trigger (RX -> phone).
 *
 * NimBLE GATT peripheral with a single notify characteristic. No pairing,
 * no phone app required to test today: any generic BLE scanner (nRF
 * Connect, LightBlue) can connect, subscribe, and watch the value change.
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WISENSE_BLE_TRIGGER_IDLE   = 0,
    WISENSE_BLE_TRIGGER_ALERT  = 1,
    WISENSE_BLE_TRIGGER_CANCEL = 2,
} wisense_ble_trigger_evt_t;

/** @brief Start the NimBLE stack, GATT service, and advertising. */
esp_err_t wisense_ble_trigger_init(void);

/**
 * @brief Update the trigger characteristic and notify a subscribed phone.
 *
 * Safe to call with no phone connected/subscribed yet — the value is still
 * updated so a client that connects later can read the current state; this
 * only logs a warning rather than failing.
 */
esp_err_t wisense_ble_trigger_send(wisense_ble_trigger_evt_t evt);

#ifdef __cplusplus
}
#endif
