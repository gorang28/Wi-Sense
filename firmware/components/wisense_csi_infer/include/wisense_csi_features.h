/*
 * CSI packet -> feature vector, mirroring python/preprocess/preprocess_csi.py.
 *
 * Every constant and every rejection rule here has a counterpart in the Python
 * preprocessing used to build the training set.  They must stay in sync: a
 * packet the PC would have dropped but the device keeps (or vice versa) shifts
 * the feature distribution away from what the trees were fitted on, which
 * degrades accuracy silently rather than failing loudly.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "wisense_csi_model.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Per-packet quality limits — preprocess_csi.py lines 62-71. */
#define WISENSE_CSI_MAX_VALID_IQ_ABS      300.0f
#define WISENSE_CSI_MAX_VALID_AMP         500.0f
#define WISENSE_CSI_RAW_AMP_CAP         32767.0f
#define WISENSE_CSI_RAW_MEDIAN_SCALE      500.0f
#define WISENSE_CSI_RAW_TARGET_MEDIAN      40.0f
#define WISENSE_CSI_RAW_MIN_GOOD_FRAC       0.50f
#define WISENSE_CSI_MIN_VALID_SC_GOOD_FRAC  0.85f
#define WISENSE_CSI_ARTIFACT_PLATEAU_LO  2560.0f
#define WISENSE_CSI_ARTIFACT_PLATEAU_HI  2600.0f
#define WISENSE_CSI_ARTIFACT_MEAN_MIN     400.0f

/* Post-baseline packet limit — preprocess_csi.py MAX_NORM_PKT_ABS. */
#define WISENSE_CSI_MAX_NORM_PKT_ABS      600.0f

/**
 * @brief Convert one raw CSI packet into WISENSE_CSI_NUM_SC amplitudes.
 *
 * Applies the same guard-tone mask, corruption checks and raw-int16 rescale as
 * parse_csi_amplitude() / is_corrupt_iq_packet().
 *
 * @param csi Gain-compensated interleaved I/Q, as written to the binary stream.
 * @param len Number of int16 samples; must be WISENSE_CSI_NUM_COMPLEX * 2.
 * @param out_amplitude Receives WISENSE_CSI_NUM_SC amplitudes.
 * @return true when the packet is usable; false when it fails a quality check.
 */
bool wisense_csi_packet_amplitude(const int16_t *csi, uint16_t len, float *out_amplitude);

/**
 * @brief A run of packets inside a circular buffer.
 *
 * Windowing evicts from the tail and appends at the head, so the rows wrap.
 * Describing the ring rather than requiring a contiguous copy avoids a second
 * 30 KB buffer, which internal SRAM has no room for once WiFi and NimBLE have
 * taken their share.  Set @c capacity equal to @c count and @c start to 0 to
 * pass an ordinary flat array.
 */
typedef struct {
    const float *storage;  /*!< capacity * WISENSE_CSI_NUM_SC floats. */
    uint16_t capacity;
    uint16_t start;        /*!< Row index of the oldest packet. */
    uint16_t count;        /*!< Packets in the window. */
} wisense_csi_window_t;

/** @brief Row of @p window at position @p index, honouring the wrap. */
static inline const float *wisense_csi_window_row(const wisense_csi_window_t *window,
                                                  uint16_t index)
{
    uint16_t row = window->start + index;
    if (row >= window->capacity) {
        row -= window->capacity;
    }
    return &window->storage[(uint32_t)row * WISENSE_CSI_NUM_SC];
}

/**
 * @brief Build the 75-dimensional feature vector for one window.
 *
 * Mirrors extract_features(); the baseline must already be subtracted.
 *
 * @param window Packets to summarise; @c count must be at least 1.
 * @param out_features Receives WISENSE_CSI_FEATURE_DIM values.
 */
void wisense_csi_window_features(const wisense_csi_window_t *window, float *out_features);

/**
 * @brief Median of @p values, reordering them in place.
 *
 * numpy.median semantics: an even count averages the two middle values.
 */
float wisense_csi_median_inplace(float *values, int count);

#ifdef __cplusplus
}
#endif
