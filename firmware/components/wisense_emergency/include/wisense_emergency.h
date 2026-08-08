/*
 * WiSense fall emergency (Modules 4+5).
 *
 * Fall detection triggers a 15 s countdown on the OLED.
 * Push button cancels before TX notification (ESP-NOW lands in a later module).
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"
#include "wisense_classifier.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Initialize cancel button GPIO and countdown timer. Pass -1 for Kconfig default GPIO. */
esp_err_t wisense_emergency_init(int button_gpio);

/**
 * @brief Handle a new prediction class (call from classifier on_change).
 *
 * Starts emergency countdown when class becomes Fall.
 */
esp_err_t wisense_emergency_on_class_change(wisense_class_t new_class);

/** True while countdown is running or TX alert was triggered and not yet cleared. */
bool wisense_emergency_is_active(void);

#ifdef __cplusplus
}
#endif
