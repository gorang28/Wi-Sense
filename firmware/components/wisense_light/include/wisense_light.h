/*
 * WiSense light automation — relay + digital LDR.
 *
 * Policy:
 *   Empty -> Presence/Motion: turn relay ON only if dark; if bright, watch LDR.
 *   While Presence/Motion: poll LDR and turn relay ON when room becomes dark.
 *   Presence + FSR pressure (on bed): sleeping — keep relay OFF.
 *   Presence/Motion -> Empty: turn relay OFF after EMPTY_OFF_SEC if still Empty.
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"
#include "wisense_classifier.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize relay output and LDR digital input.
 *
 * Pass -1 for either GPIO to use Kconfig defaults (relay=26, LDR=32).
 */
esp_err_t wisense_light_init(int relay_gpio, int ldr_gpio);

/** Force relay OFF (safe idle state). */
esp_err_t wisense_light_relay_off(void);

/** Force relay ON (for bench tests). */
esp_err_t wisense_light_relay_on(void);

/** True when the LDR module reports a dark room. */
bool wisense_light_is_dark(void);

/**
 * @brief Apply Module 2 automation for a new prediction class.
 *
 * Tracks the previous class internally. Call from the classifier change callback.
 */
esp_err_t wisense_light_on_class_change(wisense_class_t new_class);

#ifdef __cplusplus
}
#endif
