/*
 * On-device port of python/live/live_cascade_detect.py.
 *
 * The decision layer is deliberately a line-by-line port rather than a
 * reinterpretation: the smoothing factor, the asymmetric debounce and the fall
 * rule were all tuned against recorded runs on this hardware, and changing any
 * of them here would invalidate that tuning without any way to re-measure it
 * on device.
 */
#include <inttypes.h>
#include <math.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "wisense_csi_features.h"
#include "wisense_csi_infer.h"
#include "wisense_csi_model.h"
#include "wisense_csi_selftest.h"

static const char *TAG = "csi_infer";

#define NUM_SC          WISENSE_CSI_NUM_SC
#define FEATURE_DIM     WISENSE_CSI_FEATURE_DIM
#define RING_PACKETS    CONFIG_WISENSE_CSI_RING_PACKETS
#define QUEUE_DEPTH     CONFIG_WISENSE_CSI_QUEUE_DEPTH

/* Tunables arrive as Kconfig integers so they can be set without a float
 * parser in menuconfig; scale them back here. */
#define CFG_FLOAT(name) ((float)(CONFIG_##name) / 1000.0f)

#define SMOOTHING          CFG_FLOAT(WISENSE_CSI_SMOOTHING_MILLI)
#define MIN_COVERAGE       CFG_FLOAT(WISENSE_CSI_MIN_COVERAGE_MILLI)

#define WINDOW_US       ((uint64_t)CONFIG_WISENSE_CSI_WINDOW_MS * 1000ULL)
#define HOP_US          ((uint64_t)CONFIG_WISENSE_CSI_HOP_MS * 1000ULL)

/* The fall tunables only exist in Kconfig when the rule is selected, so they
 * must not be referenced in the other two fall modes. */
#if CONFIG_WISENSE_CSI_FALL_MODE_RULE
#define FALL_MOTION_HIGH   CFG_FLOAT(WISENSE_CSI_FALL_MOTION_HIGH_MILLI)
#define FALL_MOTION_LOW    CFG_FLOAT(WISENSE_CSI_FALL_MOTION_LOW_MILLI)
#define FALL_LOOKBACK_US ((uint64_t)CONFIG_WISENSE_CSI_FALL_LOOKBACK_MS * 1000ULL)
#define FALL_HOLD_US     ((uint64_t)CONFIG_WISENSE_CSI_FALL_HOLD_MS * 1000ULL)
#endif

/* live_cascade_detect.py: a delta of 3 still counts as continuous. */
#define MAX_SEQUENCE_GAP 2

/* 3 s of lookback at a 0.25 s hop needs 12 entries; 32 leaves generous slack. */
#define MOTION_HISTORY_MAX 32

typedef struct {
    uint32_t timestamp_us;
    uint32_t packet_id;
    uint16_t len;
    int16_t csi[WISENSE_CSI_NUM_COMPLEX * 2];
} csi_packet_msg_t;

typedef struct {
    uint64_t timestamp_us;
    float value;
} motion_sample_t;

typedef struct {
    float smoothed[3];
    bool smoothed_valid[3];
    bool occupied;
    bool motion;
    int occupied_pending;
    int motion_pending;
    int fall_streak;
    int still_run;
    bool fall_held;
    uint64_t fall_until_us;
    motion_sample_t history[MOTION_HISTORY_MAX];
    int history_head;
    int history_count;
} decision_state_t;

static QueueHandle_t s_queue;
static SemaphoreHandle_t s_mutex;
static TaskHandle_t s_task;
static bool s_initialised;

/* Ring of baseline-subtracted amplitudes.  Doubles as the calibration buffer:
 * calibration always finishes before the first window is assembled, so the two
 * uses never overlap and internal SRAM only pays for one of them. */
static float *s_ring;
static uint64_t s_ring_timestamp[RING_PACKETS];
static uint16_t s_ring_segment[RING_PACKETS];
static uint16_t s_ring_start;
static uint16_t s_ring_count;

static float s_baseline[NUM_SC];
static wisense_csi_state_t s_state = WISENSE_CSI_STATE_IDLE;
static wisense_csi_class_t s_class = WISENSE_CSI_CLASS_EMPTY;
static wisense_csi_on_class_cb_t s_on_class;
static void *s_on_class_ctx;

static uint32_t s_dropped_queue;
static uint32_t s_rejected_packets;
static uint32_t s_accepted_packets;
static volatile bool s_recalibrate_request;

/* ---------- timestamp / sequence bookkeeping (mirrors the PC path) ---------- */

static bool s_have_previous_raw;
static uint32_t s_previous_raw_timestamp;
static uint64_t s_elapsed_us;
static bool s_have_previous_id;
static uint32_t s_previous_packet_id;
static uint16_t s_segment;

/**
 * Turn the wrapping uint32 CSI timestamp into a monotonic microsecond clock.
 *
 * Returns false on a backwards jump larger than half the range, which means a
 * receiver reset rather than a wrap — the caller must then start a fresh
 * window instead of bridging unrelated samples.
 */
static bool unwrap_timestamp(uint32_t raw, uint64_t *out_elapsed)
{
    if (!s_have_previous_raw) {
        s_have_previous_raw = true;
        s_previous_raw_timestamp = raw;
        *out_elapsed = s_elapsed_us;
        return true;
    }

    const uint32_t delta = raw - s_previous_raw_timestamp; /* wraps naturally */
    s_previous_raw_timestamp = raw;

    if (delta > 0x80000000u) {
        s_elapsed_us = 0;
        return false;
    }
    s_elapsed_us += delta;
    *out_elapsed = s_elapsed_us;
    return true;
}

static bool starts_new_capture_segment(uint32_t packet_id)
{
    if (!s_have_previous_id) {
        s_have_previous_id = true;
        s_previous_packet_id = packet_id;
        return false;
    }
    const uint32_t delta = packet_id - s_previous_packet_id;
    s_previous_packet_id = packet_id;
    return delta == 0 || delta > (uint32_t)(MAX_SEQUENCE_GAP + 1);
}

/* ---------------------------- decision layer ---------------------------- */

static void decision_reset(decision_state_t *state)
{
    memset(state, 0, sizeof(*state));
}

/**
 * Exponential moving average of a stage probability.
 *
 * Single-window probabilities swing hard near a decision boundary; averaging
 * before thresholding is what removes the flicker.  Debouncing the boolean
 * alone only delays it.
 */
static float smooth_probability(decision_state_t *state, int index, float value)
{
    if (SMOOTHING >= 1.0f) {
        return value;
    }
    if (!state->smoothed_valid[index]) {
        state->smoothed_valid[index] = true;
        state->smoothed[index] = value;
        return value;
    }
    state->smoothed[index] = SMOOTHING * value + (1.0f - SMOOTHING) * state->smoothed[index];
    return state->smoothed[index];
}

/**
 * Flip a latched boolean only after enough consecutive disagreements.
 *
 * Asymmetric on purpose: entering a state should be quick, leaving it should
 * need more evidence, because a person sitting perfectly still produces a weak
 * wandering signal that a symmetric rule keeps dropping to EMPTY.
 */
static bool debounce(bool *latched, int *pending, bool raw, int on_required, int off_required)
{
    if (raw == *latched) {
        *pending = 0;
    } else {
        (*pending)++;
        if (*pending >= (raw ? on_required : off_required)) {
            *latched = raw;
            *pending = 0;
        }
    }
    return *latched;
}

#if CONFIG_WISENSE_CSI_FALL_MODE_RULE
static void history_push(decision_state_t *state, uint64_t timestamp_us, float value)
{
    const int slot = (state->history_head + state->history_count) % MOTION_HISTORY_MAX;
    if (state->history_count < MOTION_HISTORY_MAX) {
        state->history_count++;
    } else {
        state->history_head = (state->history_head + 1) % MOTION_HISTORY_MAX;
    }
    state->history[slot].timestamp_us = timestamp_us;
    state->history[slot].value = value;
}

static void history_trim(decision_state_t *state, uint64_t now_us)
{
    while (state->history_count > 0) {
        const motion_sample_t *oldest = &state->history[state->history_head];
        if (now_us - oldest->timestamp_us <= FALL_LOOKBACK_US) {
            break;
        }
        state->history_head = (state->history_head + 1) % MOTION_HISTORY_MAX;
        state->history_count--;
    }
}

/**
 * Heuristic fall: a burst of motion that stops abruptly while someone is still
 * in the room.
 *
 * The trained fall stage does not generalise — ~127 positive windows from ten
 * recordings of one fall at one spot, scoring held-out falls at 0.55 mean — so
 * this keys off the occupied and motion stages, which have orders of magnitude
 * more data behind them.  It is a heuristic, not a classifier: abruptly
 * stopping a brisk walk produces the same signature.
 */
static bool rule_fall(decision_state_t *state, uint64_t timestamp_us,
                      float motion_probability, bool occupied)
{
    history_push(state, timestamp_us, motion_probability);
    history_trim(state, timestamp_us);

    if (state->fall_held) {
        if (timestamp_us < state->fall_until_us && occupied) {
            return true;
        }
        state->fall_held = false;
    }

    if (!occupied || state->history_count < 3) {
        state->still_run = 0;
        return false;
    }

    /* Require stillness to persist, so a momentary dip mid-movement cannot
     * raise an alarm on its own. */
    if (motion_probability <= FALL_MOTION_LOW) {
        state->still_run++;
    } else {
        state->still_run = 0;
    }

    float peak = 0.0f;
    for (int i = 0; i < state->history_count; i++) {
        const int slot = (state->history_head + i) % MOTION_HISTORY_MAX;
        if (state->history[slot].value > peak) {
            peak = state->history[slot].value;
        }
    }

    if (peak >= FALL_MOTION_HIGH
        && state->still_run >= CONFIG_WISENSE_CSI_FALL_STILL_CONSECUTIVE) {
        state->fall_held = true;
        state->fall_until_us = timestamp_us + FALL_HOLD_US;
        state->still_run = 0;
        state->history_head = 0;
        state->history_count = 0;
        return true;
    }
    return false;
}
#endif /* CONFIG_WISENSE_CSI_FALL_MODE_RULE */

static wisense_csi_class_t decide(decision_state_t *state, const float *features,
                                  uint64_t timestamp_us, wisense_csi_prediction_t *out)
{
    const float raw_occupied = wisense_gbdt_predict_proba(&wisense_csi_model_occupied, features);
    const float raw_motion = wisense_gbdt_predict_proba(&wisense_csi_model_motion, features);
    const float raw_fall = wisense_gbdt_predict_proba(&wisense_csi_model_fall, features);

    const float p_occupied = smooth_probability(state, 0, raw_occupied);
    const float p_motion = smooth_probability(state, 1, raw_motion);
    const float p_fall = smooth_probability(state, 2, raw_fall);

    const bool occupied = debounce(&state->occupied, &state->occupied_pending,
                                   p_occupied >= WISENSE_CSI_THRESHOLD_OCCUPIED,
                                   CONFIG_WISENSE_CSI_OCCUPIED_CONSECUTIVE,
                                   CONFIG_WISENSE_CSI_OCCUPIED_OFF_CONSECUTIVE);
    const bool motion = debounce(&state->motion, &state->motion_pending,
                                 p_motion >= WISENSE_CSI_THRESHOLD_MOTION,
                                 CONFIG_WISENSE_CSI_MOTION_CONSECUTIVE,
                                 CONFIG_WISENSE_CSI_MOTION_CONSECUTIVE);

    state->fall_streak = (p_fall >= WISENSE_CSI_THRESHOLD_FALL) ? state->fall_streak + 1 : 0;

    bool fall = false;
#if CONFIG_WISENSE_CSI_FALL_MODE_RULE
    fall = rule_fall(state, timestamp_us, p_motion, occupied);
#elif CONFIG_WISENSE_CSI_FALL_MODE_ML
    fall = state->fall_streak >= CONFIG_WISENSE_CSI_FALL_CONSECUTIVE;
#endif

    out->p_occupied = p_occupied;
    out->p_motion = p_motion;
    out->p_fall = p_fall;

    /* Fall is tested first: the other stages cannot describe a fall, and a
     * fall that is reported as MOTION never reaches the emergency path. */
    if (fall) {
        return WISENSE_CSI_CLASS_FALL;
    }
    if (!occupied) {
        return WISENSE_CSI_CLASS_EMPTY;
    }
    return motion ? WISENSE_CSI_CLASS_MOTION : WISENSE_CSI_CLASS_PRESENCE;
}

/* ------------------------------ public state ------------------------------ */

static void publish_class(wisense_csi_class_t cls, const wisense_csi_prediction_t *detail)
{
    wisense_csi_on_class_cb_t callback = NULL;
    void *ctx = NULL;

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    const bool changed = (s_class != cls);
    s_class = cls;
    callback = s_on_class;
    ctx = s_on_class_ctx;
    xSemaphoreGive(s_mutex);

    if (changed && callback != NULL) {
        callback(cls, detail, ctx);
    }
}

wisense_csi_class_t wisense_csi_infer_get_class(void)
{
    if (s_mutex == NULL) {
        return WISENSE_CSI_CLASS_EMPTY;
    }
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    const wisense_csi_class_t cls = s_class;
    xSemaphoreGive(s_mutex);
    return cls;
}

esp_err_t wisense_csi_infer_set_class(wisense_csi_class_t cls)
{
    if (cls < WISENSE_CSI_CLASS_EMPTY || cls > WISENSE_CSI_CLASS_FALL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_mutex == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    const wisense_csi_prediction_t detail = {0};
    publish_class(cls, &detail);
    return ESP_OK;
}

esp_err_t wisense_csi_infer_set_on_class(wisense_csi_on_class_cb_t cb, void *ctx)
{
    if (s_mutex == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_mutex, portMAX_DELAY);
    s_on_class = cb;
    s_on_class_ctx = ctx;
    xSemaphoreGive(s_mutex);
    return ESP_OK;
}

wisense_csi_state_t wisense_csi_infer_get_state(void)
{
    return s_state;
}

/* ------------------------------ inference task ------------------------------ */

static void ring_reset(void)
{
    s_ring_start = 0;
    s_ring_count = 0;
}

static void ring_push(uint64_t timestamp_us, uint16_t segment, const float *amplitude)
{
    uint16_t slot;
    if (s_ring_count < RING_PACKETS) {
        slot = (s_ring_start + s_ring_count) % RING_PACKETS;
        s_ring_count++;
    } else {
        slot = s_ring_start;
        s_ring_start = (s_ring_start + 1) % RING_PACKETS;
    }
    memcpy(&s_ring[(uint32_t)slot * NUM_SC], amplitude, sizeof(float) * NUM_SC);
    s_ring_timestamp[slot] = timestamp_us;
    s_ring_segment[slot] = segment;
}

static void ring_pop_oldest(void)
{
    if (s_ring_count == 0) {
        return;
    }
    s_ring_start = (s_ring_start + 1) % RING_PACKETS;
    s_ring_count--;
}

static uint64_t ring_oldest_timestamp(void)
{
    return s_ring_timestamp[s_ring_start];
}

static uint16_t ring_oldest_segment(void)
{
    return s_ring_segment[s_ring_start];
}

/**
 * Build the empty-room baseline from the packets collected during calibration.
 *
 * Per-subcarrier median, matching the PC path.  Only RING_PACKETS samples are
 * retained; when more were requested they are subsampled evenly across the
 * whole calibration period, so the median still reflects the full duration
 * rather than only its first seconds.
 */
static void finish_calibration(uint16_t stored)
{
    /* static: 1 KB is a lot to put on the inference task's stack, which also
     * has to carry the change callback's OLED / BLE / buzzer chain. */
    static float scratch[RING_PACKETS];

    for (int sc = 0; sc < NUM_SC; sc++) {
        for (uint16_t packet = 0; packet < stored; packet++) {
            scratch[packet] = s_ring[(uint32_t)packet * NUM_SC + sc];
        }
        s_baseline[sc] = wisense_csi_median_inplace(scratch, stored);
    }
    ring_reset();
}

static void inference_task(void *arg)
{
    (void)arg;

    /* static: one packet message is ~776 bytes and this task is a singleton.
     * Keeping it off the stack leaves room for the change callback, which runs
     * OLED (I2C), buzzer, servo and BLE work on this same stack. */
    static csi_packet_msg_t msg;
    static decision_state_t state;
    float amplitude[NUM_SC];
    float features[FEATURE_DIM];
    decision_reset(&state);

    const uint32_t requested = (CONFIG_WISENSE_CSI_CALIBRATION_PACKETS > 0)
                                   ? CONFIG_WISENSE_CSI_CALIBRATION_PACKETS
                                   : 1;
    const uint32_t stride = (requested + RING_PACKETS - 1) / RING_PACKETS;
    uint32_t calibration_seen = 0;
    uint16_t calibration_stored = 0;
    uint64_t last_prediction_us = 0;
    bool have_prediction = false;
    int64_t grace_until_us = 0;
    bool announced_calibration = false;
    int64_t last_tick_us = 0;
    int64_t last_packet_us = esp_timer_get_time();
    int64_t last_starved_us = 0;

    if (CONFIG_WISENSE_CSI_GRACE_SEC > 0) {
        s_state = WISENSE_CSI_STATE_GRACE;
        grace_until_us = esp_timer_get_time()
                         + (int64_t)CONFIG_WISENSE_CSI_GRACE_SEC * 1000000;
        ESP_LOGW(TAG, ">>> LEAVE THE ROOM - empty-room calibration starts in %ds <<<",
                 CONFIG_WISENSE_CSI_GRACE_SEC);
    } else {
        s_state = WISENSE_CSI_STATE_CALIBRATING;
    }

    for (;;) {
        if (s_recalibrate_request) {
            s_recalibrate_request = false;
            calibration_seen = 0;
            calibration_stored = 0;
            announced_calibration = false;
            have_prediction = false;
            ring_reset();
            decision_reset(&state);
            s_state = WISENSE_CSI_STATE_GRACE;
            grace_until_us = esp_timer_get_time()
                             + (int64_t)CONFIG_WISENSE_CSI_GRACE_SEC * 1000000;
            ESP_LOGW(TAG, ">>> Recalibrating - LEAVE THE ROOM (%ds) <<<",
                     CONFIG_WISENSE_CSI_GRACE_SEC);
        }

        const bool have_packet =
            (xQueueReceive(s_queue, &msg, pdMS_TO_TICKS(500)) == pdTRUE);

        /*
         * The grace countdown must run on wall-clock time, not on packet
         * arrivals: if the transmitter is off there are no packets, and tying
         * the countdown to them would leave the board waiting forever with no
         * indication of why.
         */
        if (s_state == WISENSE_CSI_STATE_GRACE) {
            const int64_t now = esp_timer_get_time();
            const int64_t remaining = grace_until_us - now;
            if (remaining > 0) {
                /* 900 ms, not 1 s: the loop only wakes every 500 ms, so a full
                 * second threshold slips to 1.5 s and the countdown visibly
                 * skips numbers. */
                if (now - last_tick_us >= 900000) {
                    last_tick_us = now;
                    ESP_LOGW(TAG, ">>> LEAVE THE ROOM - calibration in %ds <<<",
                             (int)((remaining + 999999) / 1000000));
                }
                continue;
            }
            s_state = WISENSE_CSI_STATE_CALIBRATING;
        }

        if (!have_packet) {
            /* Silence here almost always means the TX board is not powered or
             * is on another channel. Say so rather than looking hung. */
            const int64_t now = esp_timer_get_time();
            if (now - last_packet_us > 5000000 && now - last_starved_us > 5000000) {
                last_starved_us = now;
                ESP_LOGW(TAG, "no CSI packets for %ds - is the TX board powered "
                              "and on channel %d?",
                         (int)((now - last_packet_us) / 1000000),
                         CONFIG_WISENSE_CSI_EXPECTED_CHANNEL);
            }
            continue;
        }
        last_packet_us = esp_timer_get_time();

        uint64_t timestamp_us = 0;
        const bool timestamp_ok = unwrap_timestamp(msg.timestamp_us, &timestamp_us);
        if (starts_new_capture_segment(msg.packet_id)) {
            s_segment++;
        }
        if (!timestamp_ok) {
            /* Never bridge a receiver reset; start a fresh window. */
            if (s_ring_count > 0) {
                ESP_LOGD(TAG, "stream gap - rebuilding window");
            }
            ring_reset();
            have_prediction = false;
            s_rejected_packets++;
            continue;
        }

        if (!wisense_csi_packet_amplitude(msg.csi, msg.len, amplitude)) {
            s_rejected_packets++;
            continue;
        }

        if (s_state == WISENSE_CSI_STATE_CALIBRATING) {
            if (!announced_calibration) {
                announced_calibration = true;
                ESP_LOGW(TAG, ">>> Calibrating now - keep the room empty <<<");
            }
            if ((calibration_seen % stride) == 0 && calibration_stored < RING_PACKETS) {
                memcpy(&s_ring[(uint32_t)calibration_stored * NUM_SC], amplitude,
                       sizeof(amplitude));
                calibration_stored++;
            }
            calibration_seen++;
            if (calibration_seen < requested) {
                continue;
            }
            finish_calibration(calibration_stored);
            decision_reset(&state);
            have_prediction = false;
            s_state = WISENSE_CSI_STATE_RUNNING;
            ESP_LOGI(TAG, "Calibration complete (%u packets, %u retained) - detection active",
                     (unsigned)calibration_seen, (unsigned)calibration_stored);
            continue;
        }

        /* Baseline subtraction, then the same post-baseline packet limit the
         * training pipeline applies. */
        float peak = 0.0f;
        for (int sc = 0; sc < NUM_SC; sc++) {
            amplitude[sc] -= s_baseline[sc];
            const float magnitude = fabsf(amplitude[sc]);
            if (magnitude > peak) {
                peak = magnitude;
            }
        }
        if (peak > WISENSE_CSI_MAX_NORM_PKT_ABS) {
            s_rejected_packets++;
            continue;
        }
        s_accepted_packets++;

        ring_push(timestamp_us, s_segment, amplitude);

        while (s_ring_count > 0 && timestamp_us - ring_oldest_timestamp() > WINDOW_US) {
            ring_pop_oldest();
        }
        /* Content-filtered packets do not advance the segment, so this only
         * discards samples separated by genuine transmitter packet loss. */
        while (s_ring_count > 0 && ring_oldest_segment() != s_segment) {
            ring_pop_oldest();
        }
        if (s_ring_count < 2) {
            continue;
        }

        const uint64_t span_us = timestamp_us - ring_oldest_timestamp();
        if (span_us < (uint64_t)(WINDOW_US * MIN_COVERAGE)) {
            continue;
        }
        if (have_prediction && timestamp_us - last_prediction_us < HOP_US) {
            continue;
        }
        last_prediction_us = timestamp_us;
        have_prediction = true;

        const wisense_csi_window_t window = {
            .storage = s_ring,
            .capacity = RING_PACKETS,
            .start = s_ring_start,
            .count = s_ring_count,
        };
        wisense_csi_window_features(&window, features);

        wisense_csi_prediction_t detail = {0};
        const wisense_csi_class_t cls = decide(&state, features, timestamp_us, &detail);
        detail.window_packets = s_ring_count;
        detail.window_span_us = (uint32_t)span_us;

        publish_class(cls, &detail);

#if CONFIG_WISENSE_CSI_LOG_PREDICTIONS
        ESP_LOGI(TAG, "%-8s occ=%.2f mot=%.2f fall=%.2f | samples=%u span=%.2fs "
                      "acc=%" PRIu32 " rej=%" PRIu32 " drop=%" PRIu32,
                 (cls == WISENSE_CSI_CLASS_FALL)     ? "FALL"
                 : (cls == WISENSE_CSI_CLASS_MOTION) ? "MOTION"
                 : (cls == WISENSE_CSI_CLASS_EMPTY)  ? "EMPTY"
                                                     : "PRESENCE",
                 detail.p_occupied, detail.p_motion, detail.p_fall,
                 (unsigned)detail.window_packets, span_us / 1000000.0f,
                 s_accepted_packets, s_rejected_packets, s_dropped_queue);
#endif
    }
}

/* --------------------------------- lifecycle --------------------------------- */

void wisense_csi_infer_submit(const void *csi, uint16_t len,
                              uint32_t timestamp_us, uint32_t packet_id)
{
    if (s_queue == NULL || csi == NULL || len != WISENSE_CSI_NUM_COMPLEX * 2) {
        return;
    }

    csi_packet_msg_t msg;
    msg.timestamp_us = timestamp_us;
    msg.packet_id = packet_id;
    msg.len = len;
    memcpy(msg.csi, csi, sizeof(int16_t) * len);

    /* WiFi task context — a full queue drops the newest packet rather than
     * backpressuring CSI capture, matching the binary stream's writer queue. */
    if (xQueueSend(s_queue, &msg, 0) != pdTRUE) {
        s_dropped_queue++;
    }
}

esp_err_t wisense_csi_infer_init(void)
{
    if (s_initialised) {
        return ESP_OK;
    }

    /* The project pins the default log level to ERROR so peripheral chatter
     * cannot corrupt the binary CSI stream.  Raise just this component's tag,
     * otherwise the detector runs completely silently. */
    esp_log_level_set(TAG, ESP_LOG_INFO);
    /* Separate tag, so it needs raising too — otherwise a PASS is invisible and
     * only a failure ever prints, which reads as "the self-test never ran". */
    esp_log_level_set("csi_selftest", ESP_LOG_INFO);

    s_mutex = xSemaphoreCreateMutex();
    if (s_mutex == NULL) {
        return ESP_ERR_NO_MEM;
    }

    s_ring = heap_caps_malloc((size_t)RING_PACKETS * NUM_SC * sizeof(float),
                              MALLOC_CAP_8BIT);
    if (s_ring == NULL) {
        ESP_LOGE(TAG, "no room for the %u-packet CSI ring (%u bytes)",
                 (unsigned)RING_PACKETS,
                 (unsigned)((size_t)RING_PACKETS * NUM_SC * sizeof(float)));
        vSemaphoreDelete(s_mutex);
        s_mutex = NULL;
        return ESP_ERR_NO_MEM;
    }

    s_queue = xQueueCreate(QUEUE_DEPTH, sizeof(csi_packet_msg_t));
    if (s_queue == NULL) {
        heap_caps_free(s_ring);
        s_ring = NULL;
        vSemaphoreDelete(s_mutex);
        s_mutex = NULL;
        return ESP_ERR_NO_MEM;
    }

    memcpy(s_baseline, wisense_csi_reference_baseline, sizeof(s_baseline));
    ring_reset();
    s_class = WISENSE_CSI_CLASS_EMPTY;
    s_state = WISENSE_CSI_STATE_IDLE;
    s_initialised = true;

    ESP_LOGI(TAG, "init: %u-packet ring (%u KB), queue depth %u, %u-dim features",
             (unsigned)RING_PACKETS,
             (unsigned)((size_t)RING_PACKETS * NUM_SC * sizeof(float) / 1024),
             (unsigned)QUEUE_DEPTH, (unsigned)FEATURE_DIM);

#if CONFIG_WISENSE_CSI_SELFTEST
    /* Proves the compiled trees still reproduce the PC model.  A mismatch here
     * means the exported tables and the firmware have drifted apart, which
     * would otherwise look like the model quietly getting worse on device. */
    if (wisense_csi_selftest_run(1e-3f) != ESP_OK) {
        ESP_LOGE(TAG, "model self-test FAILED - predictions are not trustworthy");
    }
#endif
    return ESP_OK;
}

esp_err_t wisense_csi_infer_start(void)
{
    if (!s_initialised) {
        return ESP_ERR_INVALID_STATE;
    }
    if (s_task != NULL) {
        return ESP_OK;
    }

    /*
     * 8192 bytes. The keyboard placeholder needed 6144 for the change callback
     * alone (OLED I2C, esp_timer, buzzer and servo all run synchronously on the
     * caller's stack, and 3072 was measured to overflow it). This task adds the
     * packet parser's two 166-float scratch arrays on top of that chain, so it
     * needs more headroom than the placeholder did, not less.
     */
    if (xTaskCreate(inference_task, "csi_infer", 8192, NULL, 4, &s_task) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t wisense_csi_infer_recalibrate(void)
{
    if (!s_initialised) {
        return ESP_ERR_INVALID_STATE;
    }
    /* Picked up by the inference task at the top of its next iteration, so the
     * baseline is only ever rewritten from that task. */
    s_recalibrate_request = true;
    return ESP_OK;
}
