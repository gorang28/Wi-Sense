/*
 * TX fall-emergency orchestrator (TX-1).
 *
 * Runs in a background task so CSI ESP-NOW send in app_main is unaffected.
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Start orchestrator worker task. Call once before ESP-NOW emergency recv. */
esp_err_t wisense_emergency_tx_init(void);

/** Queue a FALL_ALERT from the ESP-NOW callback (non-blocking). */
void wisense_emergency_tx_on_alert(void);

/** Queue a FALL_CANCEL from the ESP-NOW callback (non-blocking). */
void wisense_emergency_tx_on_cancel(void);

/** True while alert startup or active streaming emergency is in progress. */
bool wisense_emergency_tx_is_active(void);

#ifdef __cplusplus
}
#endif
