/*
 * On-device CSI cascade inference.
 *
 * Runs the same pipeline as python/live/live_cascade_detect.py — empty-room
 * calibration, timestamped 2 s windows, three gradient-boosted stages, EMA
 * smoothing, asymmetric debounce and the heuristic fall rule — against the
 * trees exported into wisense_csi_model.c.
 *
 * This component deliberately does not include wisense_classifier.h.  The
 * classifier component depends on this one, so depending back would be
 * circular; classifier_tinyml.c maps wisense_csi_class_t onto wisense_class_t
 * instead (the enumerators are kept in the same order).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Same order and values as wisense_class_t. */
typedef enum {
    WISENSE_CSI_CLASS_EMPTY = 0,
    WISENSE_CSI_CLASS_PRESENCE,
    WISENSE_CSI_CLASS_MOTION,
    WISENSE_CSI_CLASS_FALL,
} wisense_csi_class_t;

/** Where the inference task is in its lifecycle. */
typedef enum {
    WISENSE_CSI_STATE_IDLE = 0,
    WISENSE_CSI_STATE_GRACE,        /*!< Counting down so the room can be vacated. */
    WISENSE_CSI_STATE_CALIBRATING,  /*!< Collecting empty-room packets. */
    WISENSE_CSI_STATE_RUNNING,      /*!< Emitting predictions. */
} wisense_csi_state_t;

/** Per-prediction detail, for logging and the OLED. */
typedef struct {
    float p_occupied;
    float p_motion;
    float p_fall;
    uint16_t window_packets;
    uint32_t window_span_us;
} wisense_csi_prediction_t;

/** Invoked from the inference task whenever the reported class changes. */
typedef void (*wisense_csi_on_class_cb_t)(wisense_csi_class_t new_class,
                                          const wisense_csi_prediction_t *detail,
                                          void *ctx);

/**
 * @brief Allocate buffers and prepare the inference pipeline.
 *
 * Safe to call more than once; later calls are no-ops.
 */
esp_err_t wisense_csi_infer_init(void);

/** @brief Start the inference task.  Calibration begins after the grace period. */
esp_err_t wisense_csi_infer_start(void);

/**
 * @brief Hand one CSI packet to the inference task.
 *
 * Called from the WiFi CSI callback, so it never blocks: if the queue is full
 * the packet is dropped and counted.  @p csi must already carry the same gain
 * compensation the binary stream applies, or the device and the PC will build
 * features from different numbers.
 *
 * @param csi Interleaved int16 I/Q samples.  Typed void because the caller's
 *        copy lives inside a packed struct at an odd offset; it is copied out
 *        with memcpy rather than dereferenced as int16_t.
 * @param len Number of int16 samples in @p csi.
 * @param timestamp_us Receiver-local CSI timestamp (wrapping uint32).
 * @param packet_id Transmitter sequence counter, used to spot lost packets.
 */
void wisense_csi_infer_submit(const void *csi, uint16_t len,
                              uint32_t timestamp_us, uint32_t packet_id);

/** @brief Most recent reported class. */
wisense_csi_class_t wisense_csi_infer_get_class(void);

/** @brief Force the reported class (test hook; mirrors the placeholder's set). */
esp_err_t wisense_csi_infer_set_class(wisense_csi_class_t cls);

/** @brief Register the change callback.  Replaces any previous registration. */
esp_err_t wisense_csi_infer_set_on_class(wisense_csi_on_class_cb_t cb, void *ctx);

/** @brief Current lifecycle state. */
wisense_csi_state_t wisense_csi_infer_get_state(void);

/**
 * @brief Discard the current baseline and calibrate again.
 *
 * Use after the room is rearranged, or when everything reads occupied because
 * the baseline was captured with someone still in the room.
 */
esp_err_t wisense_csi_infer_recalibrate(void);

#ifdef __cplusplus
}
#endif
