/*
 * Internal helpers for wisense_emergency (not part of the public API).
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t wisense_emergency_button_init(int gpio);
bool wisense_emergency_button_is_pressed(void);

/** Notify TX of a confirmed fall emergency (ESP-NOW). */
void wisense_emergency_notify_tx(void);

/** Notify TX that RX cancelled an active emergency. */
void wisense_emergency_notify_tx_cancel(void);

esp_err_t wisense_emergency_buzzer_init(int gpio);
void wisense_emergency_buzzer_stop(void);
void wisense_emergency_buzzer_start_countdown(void);
void wisense_emergency_buzzer_start_alert(void);

esp_err_t wisense_emergency_indicator_init(int gpio);
void wisense_emergency_indicator_set(bool on);
void wisense_emergency_indicator_stop(void);

#ifdef __cplusplus
}
#endif
