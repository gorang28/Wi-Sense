/*
 * WiSense classifier interface.
 *
 * Swappable prediction source: UART placeholder today, TinyML later.
 * Automation and display code should only depend on this header.
 */
#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Prediction classes produced by the future TinyML model.
 *
 * There are no other classes. Keep this enum stable so modules that
 * consume predictions do not need changes when TinyML is plugged in.
 */
typedef enum {
    WISENSE_CLASS_EMPTY = 0,
    WISENSE_CLASS_PRESENCE,
    WISENSE_CLASS_MOTION,
    WISENSE_CLASS_FALL,
} wisense_class_t;

/** Callback invoked when the predicted class changes. */
typedef void (*wisense_classifier_on_change_cb_t)(wisense_class_t new_class, void *ctx);

/**
 * @brief Operations table for the active classifier implementation.
 *
 * Placeholder and TinyML backends expose the same ops. Call sites use
 * wisense_classifier_get() and never talk to UART/ML details directly.
 */
typedef struct {
    /** One-time setup (mutex, default class, UART/ML init). */
    esp_err_t (*init)(void);

    /** Start background work (UART reader task, inference loop, etc.). */
    esp_err_t (*start)(void);

    /** Thread-safe read of the current class. */
    wisense_class_t (*get)(void);

    /** Force the current class (tests / future ML push path). */
    esp_err_t (*set)(wisense_class_t cls);

    /** Register optional change notification callback. */
    esp_err_t (*set_on_change)(wisense_classifier_on_change_cb_t cb, void *ctx);
} wisense_classifier_ops_t;

/** Human-readable label for OLED / logs (e.g. "Presence"). */
const char *wisense_class_to_string(wisense_class_t cls);

/**
 * @brief Return the active classifier ops (selected at build time via Kconfig).
 */
const wisense_classifier_ops_t *wisense_classifier_get(void);

#ifdef __cplusplus
}
#endif
