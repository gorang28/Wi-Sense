/*
 * WiSense privacy-flap servo (opens to expose the phone on fall alert).
 */
#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Initialize the LEDC PWM output and move to the closed position. Pass -1 for Kconfig default GPIO. */
esp_err_t wisense_servo_init(int gpio);

/** Move the flap to the open position (fall alert active). */
esp_err_t wisense_servo_open(void);

/** Move the flap to the closed position (idle / cancelled). */
esp_err_t wisense_servo_close(void);

#ifdef __cplusplus
}
#endif
