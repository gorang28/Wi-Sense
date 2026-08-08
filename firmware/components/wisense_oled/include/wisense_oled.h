/*
 * WiSense SSD1306 OLED display (128x64, I2C).
 *
 * Module 1: show the current prediction class.
 * Module 5: fall emergency countdown overlay.
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"
#include "wisense_classifier.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize I2C master + SSD1306.
 *
 * @param i2c_sda_gpio SDA pin (plan default: GPIO21)
 * @param i2c_scl_gpio SCL pin (plan default: GPIO22)
 */
esp_err_t wisense_oled_init(int i2c_sda_gpio, int i2c_scl_gpio);

/** Clear the entire framebuffer and flush to the panel. */
esp_err_t wisense_oled_clear(void);

/** Show the prediction label centered on the display. */
esp_err_t wisense_oled_show_class(wisense_class_t cls);

/**
 * @brief Emergency countdown overlay.
 *
 * @param active   When true, draw the emergency screen.
 * @param countdown_sec  Seconds remaining (>0), or 0 for "ALERT SENT".
 */
esp_err_t wisense_oled_show_emergency(bool active, int countdown_sec);

#ifdef __cplusplus
}
#endif
