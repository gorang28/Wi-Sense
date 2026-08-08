/*
 * FSR406 force-sensitive resistor (Module 3).
 *
 * Analog read via ESP32 ADC. Higher raw value = more pressure on the sensor.
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize ADC for the FSR voltage-divider input.
 *
 * @param gpio ADC-capable GPIO (default: GPIO34). Pass -1 for Kconfig default.
 */
esp_err_t wisense_fsr_init(int gpio);

/**
 * @brief Debounced/hysteresis pressure state.
 *
 * Backed by a smoothed (moving-average) reading and a two-threshold
 * Schmitt trigger: enters "pressed" only at/above PRESS_THRESHOLD, exits
 * only at/below RELEASE_THRESHOLD, so a reading sitting near the boundary
 * doesn't chatter.
 */
bool wisense_fsr_is_pressed(void);

/** Latest raw ADC sample (0..4095 on 12-bit). */
int wisense_fsr_read_raw(void);

#ifdef __cplusplus
}
#endif
