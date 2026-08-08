/*
 * RX emergency integration for wisense_hw app.
 *
 * Wires fall detection, OLED countdown, and cancel button into app_main.
 */
#pragma once

#include "esp_err.h"
#include "wisense_classifier.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Init cancel button + emergency countdown (Kconfig GPIO defaults). */
esp_err_t emergency_rx_init(void);

/**
 * @brief Run emergency logic for a new classifier output.
 *
 * @param new_class Latest prediction class.
 * @param skip_class_oled Set to true when emergency owns the OLED (countdown active).
 */
esp_err_t emergency_rx_on_class_change(wisense_class_t new_class, bool *skip_class_oled);

#ifdef __cplusplus
}
#endif
