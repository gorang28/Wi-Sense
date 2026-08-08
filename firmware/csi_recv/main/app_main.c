/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
/* Get Start Example

   This example code is in the Public Domain (or CC0 licensed, at your option.)

   Unless required by applicable law or agreed to in writing, this
   software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
   CONDITIONS OF ANY KIND, either express or implied.
*/

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <inttypes.h>
#include <errno.h>
#include <unistd.h>

#include "nvs_flash.h"
#include "driver/usb_serial_jtag_vfs.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "esp_timer.h"

#include "esp_mac.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_csi_gain_ctrl.h"

#include "csi_binary_proto.h"

#include "wisense_classifier.h"
#if CONFIG_WISENSE_CLASSIFIER_TINYML
#include "wisense_csi_infer.h"
#endif
#include "wisense_fsr.h"
#include "wisense_light.h"
#include "wisense_oled.h"

#include "emergency_rx.h"

_Static_assert(sizeof(csi_binary_header_t) == 12, "csi_binary_header_t size mismatch");
_Static_assert(sizeof(csi_binary_legacy_payload_t) == 805, "csi_binary_legacy_payload_t size mismatch");
_Static_assert(sizeof(csi_binary_c5c6_payload_t) == 795, "csi_binary_c5c6_payload_t size mismatch");

#define CONFIG_LESS_INTERFERENCE_CHANNEL   11
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 || (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0))
#define CONFIG_WIFI_BAND_MODE               WIFI_BAND_MODE_2G_ONLY
#define CONFIG_WIFI_2G_BANDWIDTHS           WIFI_BW_HT40
#define CONFIG_WIFI_5G_BANDWIDTHS           WIFI_BW_HT40
#define CONFIG_WIFI_2G_PROTOCOL             WIFI_PROTOCOL_11N
#define CONFIG_WIFI_5G_PROTOCOL             WIFI_PROTOCOL_11N
#else
#define CONFIG_WIFI_BANDWIDTH           WIFI_BW_HT40
#endif

#define CONFIG_ESP_NOW_PHYMODE           WIFI_PHY_MODE_HT40
#define CONFIG_ESP_NOW_RATE             WIFI_PHY_RATE_MCS0_LGI
#define CONFIG_FORCE_GAIN                   0

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
#define CSI_FORCE_LLTF                      0
#endif

#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
#define CONFIG_GAIN_CONTROL                 1
#endif

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

static const uint8_t CONFIG_CSI_SEND_MAC[] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};
static const char *TAG = "csi_recv";

static void wifi_init()
{
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

#if CONFIG_IDF_TARGET_ESP32C5
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE);
    wifi_protocols_t protocols = {
        .ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
        .ghz_5g = CONFIG_WIFI_5G_PROTOCOL
    };
    ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
    wifi_bandwidths_t bandwidth = {
        .ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
        .ghz_5g = CONFIG_WIFI_5G_BANDWIDTHS
    };
    ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#elif (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) || CONFIG_IDF_TARGET_ESP32C61
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE);
    wifi_protocols_t protocols = {
        .ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
    };
    ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
    wifi_bandwidths_t bandwidth = {
        .ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
    };
    ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#else
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, CONFIG_WIFI_BANDWIDTH));
    ESP_ERROR_CHECK(esp_wifi_start());
#endif

    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
#if CONFIG_IDF_TARGET_ESP32C5
    if ((CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_2G_ONLY && CONFIG_WIFI_2G_BANDWIDTHS == WIFI_BW_HT20)
            || (CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_5G_ONLY && CONFIG_WIFI_5G_BANDWIDTHS == WIFI_BW_HT20)) {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_NONE));
    } else {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_BELOW));
    }
#elif (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) || CONFIG_IDF_TARGET_ESP32C61
    if (CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_2G_ONLY && CONFIG_WIFI_2G_BANDWIDTHS == WIFI_BW_HT20) {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_NONE));
    } else {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_BELOW));
    }
#else
    if (CONFIG_WIFI_BANDWIDTH == WIFI_BW_HT20) {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_NONE));
    } else {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_BELOW));
    }
#endif

    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, CONFIG_CSI_SEND_MAC));
}

static void wifi_esp_now_init(esp_now_peer_info_t peer)
{
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));
    esp_now_rate_config_t rate_config = {
        .phymode = CONFIG_ESP_NOW_PHYMODE,
        .rate = CONFIG_ESP_NOW_RATE,//  WIFI_PHY_RATE_MCS0_LGI,
        .ersu = false,
        .dcm = false
    };
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(peer.peer_addr, &rate_config));

}

/*
 * The binary CSI stream and ESP_LOG both go out over the single native
 * USB-Serial-JTAG peripheral on this board (no secondary console). Two
 * problems that used to exist here:
 *   1. A log line from another task (fall countdown, light/OLED/BLE
 *      transitions) could interleave mid-frame, since header+payload were
 *      two separate unsynchronized write() calls.
 *   2. csi_binary_write() ran synchronously inside wifi_csi_rx_cb (WiFi
 *      task context) — a slow/stalled USB host backpressured directly into
 *      CSI capture.
 * Fixed by: building each frame into one contiguous buffer, handing it to a
 * dedicated writer task over a queue (non-blocking send — a full queue
 * drops the newest frame and counts it, never blocks the CSI callback), and
 * routing all ESP_LOG output through the same mutex the writer task uses
 * for its write(), so a log line and a CSI frame can never interleave.
 */
#if CONFIG_WISENSE_CSI_STREAM_BINARY
#define CSI_FRAME_QUEUE_DEPTH 16

#define CSI_FRAME_MAX_PAYLOAD ( \
    sizeof(csi_binary_legacy_payload_t) > sizeof(csi_binary_c5c6_payload_t) \
        ? sizeof(csi_binary_legacy_payload_t) \
        : sizeof(csi_binary_c5c6_payload_t))
#define CSI_FRAME_MAX_BYTES (sizeof(csi_binary_header_t) + CSI_FRAME_MAX_PAYLOAD)

typedef struct {
    uint16_t len;
    uint8_t data[CSI_FRAME_MAX_BYTES];
} csi_frame_msg_t;

static QueueHandle_t s_csi_frame_queue;
static uint32_t s_csi_frame_seq;
static uint32_t s_csi_frame_dropped;
#endif /* CONFIG_WISENSE_CSI_STREAM_BINARY */

static SemaphoreHandle_t s_stdout_mutex;

/* Installed via esp_log_set_vprintf() so ESP_LOG* output takes the same
 * mutex as the CSI writer task before touching stdout. */
static int csi_console_vprintf(const char *fmt, va_list args)
{
    xSemaphoreTake(s_stdout_mutex, portMAX_DELAY);
    int ret = vprintf(fmt, args);
    xSemaphoreGive(s_stdout_mutex);
    return ret;
}

#if CONFIG_WISENSE_CSI_STREAM_BINARY
static uint16_t crc16_xmodem(const uint8_t *data, size_t len)
{
    uint16_t crc = 0x0000;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static uint32_t s_csi_frame_truncated;

/* Diagnostic only (2026-07-30): live testing showed a high CRC-fail rate on
 * the PC side even with the queue+mutex fix in place, and the queue's own
 * drop counter never fires — meaning loss is happening downstream of the
 * queue. This surfaces whether write() itself is silently returning fewer
 * bytes than requested (which would corrupt framing for this frame *and*
 * misalign the next one) so that failure mode isn't as invisible as v1's
 * total lack of any check. */
static ssize_t csi_binary_write(const void *data, size_t len)
{
    const uint8_t *bytes = data;
    size_t written = 0;

    while (written < len) {
        errno = 0;
        ssize_t n = write(STDOUT_FILENO, bytes + written, len - written);
        if (n <= 0) {
            s_csi_frame_truncated++;
            static int64_t s_last_trunc_report_us;
            int64_t now_us = esp_timer_get_time();
            if (now_us - s_last_trunc_report_us > 1000000) {
                ESP_LOGE(TAG, "short write: wrote %u/%u bytes, errno=%d - truncated %" PRIu32 " frame(s) so far",
                         (unsigned)written, (unsigned)len, errno, s_csi_frame_truncated);
                s_last_trunc_report_us = now_us;
            }
            break;
        }
        written += n;
    }
    return written;
}

static void csi_uart_writer_task(void *arg)
{
    csi_frame_msg_t msg;
    for (;;) {
        if (xQueueReceive(s_csi_frame_queue, &msg, portMAX_DELAY) == pdTRUE) {
            xSemaphoreTake(s_stdout_mutex, portMAX_DELAY);
            csi_binary_write(msg.data, msg.len);
            xSemaphoreGive(s_stdout_mutex);
        }
    }
}

/*
 * Called only from wifi_csi_rx_cb (WiFi task context) — must never block.
 * `msg` is `static`, not a stack local: this is called with an ~805-byte
 * csi_binary_legacy_payload_t already live in the caller's frame, and
 * xQueueSend() copies its argument synchronously (the CSI callback is never
 * reentered concurrently with itself), so reusing one scratch buffer across
 * calls is safe and avoids piling another ~820 bytes onto whatever WiFi/MAC
 * task stack this callback runs on.
 */
static void csi_frame_enqueue(const void *header, size_t header_len, const void *payload, size_t payload_len)
{
    static csi_frame_msg_t msg;
    if (header_len + payload_len > sizeof(msg.data)) {
        return; /* cannot happen: sized from the same structs at compile time */
    }
    memcpy(msg.data, header, header_len);
    memcpy(msg.data + header_len, payload, payload_len);
    msg.len = (uint16_t)(header_len + payload_len);

    if (xQueueSend(s_csi_frame_queue, &msg, 0) != pdTRUE) {
        s_csi_frame_dropped++;
        static int64_t s_last_report_us;
        int64_t now_us = esp_timer_get_time();
        if (now_us - s_last_report_us > 1000000) {
            ESP_LOGE(TAG, "CSI frame queue full - dropped %" PRIu32 " frame(s) so far", s_csi_frame_dropped);
            s_last_report_us = now_us;
        }
    }
}
#endif /* CONFIG_WISENSE_CSI_STREAM_BINARY */

static void csi_binary_send_legacy(uint32_t rx_id, wifi_csi_info_t *info, const wifi_pkt_rx_ctrl_t *rx_ctrl, float compensate_gain)
{
    csi_binary_legacy_payload_t payload = {0};

    payload.id = rx_id;
    memcpy(payload.mac, info->mac, 6);
    payload.rssi = rx_ctrl->rssi;
    payload.rate = rx_ctrl->rate;
    payload.sig_mode = rx_ctrl->sig_mode;
    payload.mcs = rx_ctrl->mcs;
    payload.bandwidth = rx_ctrl->cwb;
    payload.smoothing = rx_ctrl->smoothing;
    payload.not_sounding = rx_ctrl->not_sounding;
    payload.aggregation = rx_ctrl->aggregation;
    payload.stbc = rx_ctrl->stbc;
    payload.fec_coding = rx_ctrl->fec_coding;
    payload.sgi = rx_ctrl->sgi;
    payload.noise_floor = rx_ctrl->noise_floor;
    payload.ampdu_cnt = rx_ctrl->ampdu_cnt;
    payload.channel = rx_ctrl->channel;
    payload.secondary_channel = rx_ctrl->secondary_channel;
    payload.local_timestamp = rx_ctrl->timestamp;
    payload.ant = rx_ctrl->ant;
    payload.sig_len = rx_ctrl->sig_len;
    payload.rx_format = rx_ctrl->sig_mode;
    payload.len = info->len;
    payload.first_word_invalid = info->first_word_invalid;

    int count = info->len;
    if (count > CSI_BINARY_MAX_LEN) {
        count = CSI_BINARY_MAX_LEN;
    }
    if (payload.len > CSI_BINARY_MAX_LEN) {
        payload.len = CSI_BINARY_MAX_LEN;
    }
    for (int i = 0; i < count; i++) {
        payload.csi[i] = (int16_t)(compensate_gain * info->buf[i]);
    }

#if CONFIG_WISENSE_CLASSIFIER_TINYML
    /*
     * Feed the on-device model from the exact same gain-compensated samples
     * that go out over the wire, not from info->buf. The PC pipeline was
     * trained on these values, so recomputing them separately here would be
     * one more place for the device and the training set to drift apart.
     */
    wisense_csi_infer_submit(payload.csi, payload.len, payload.local_timestamp, payload.id);
#endif

#if CONFIG_WISENSE_CSI_STREAM_BINARY
    csi_binary_header_t hdr = {
        .magic = CSI_BINARY_MAGIC,
        .version = CSI_BINARY_VERSION,
        .layout = CSI_BINARY_LAYOUT_LEGACY,
        .seq = s_csi_frame_seq++,
        .payload_len = (uint16_t)sizeof(payload),
        .payload_crc16 = crc16_xmodem((const uint8_t *)&payload, sizeof(payload)),
    };

    csi_frame_enqueue(&hdr, sizeof(hdr), &payload, sizeof(payload));
#endif
}

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
static void csi_binary_send_c5c6(uint32_t rx_id, wifi_csi_info_t *info, const wifi_pkt_rx_ctrl_t *rx_ctrl,
                                 int8_t fft_gain, int8_t agc_gain, float compensate_gain)
{
    csi_binary_c5c6_payload_t payload = {0};

    payload.id = rx_id;
    memcpy(payload.mac, info->mac, 6);
    payload.rssi = rx_ctrl->rssi;
    payload.rate = rx_ctrl->rate;
    payload.noise_floor = rx_ctrl->noise_floor;
    payload.fft_gain = fft_gain;
    payload.agc_gain = agc_gain;
    payload.channel = rx_ctrl->channel;
    payload.local_timestamp = rx_ctrl->timestamp;
    payload.sig_len = rx_ctrl->sig_len;
    payload.rx_format = rx_ctrl->cur_bb_format;
    payload.first_word_invalid = info->first_word_invalid;

#if (CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61) && CSI_FORCE_LLTF
    payload.len = (info->len - 2) / 2;
    int16_t csi = ((int16_t)(((((uint16_t)info->buf[1]) << 8) | info->buf[0]) << 4) >> 4);
    payload.csi[0] = (int16_t)(compensate_gain * csi);
    for (int i = 2, j = 1; i < (info->len - 2); i += 2, j++) {
        csi = ((int16_t)(((((uint16_t)info->buf[i + 1]) << 8) | info->buf[i]) << 4) >> 4);
        if (j < CSI_BINARY_MAX_LEN) {
            payload.csi[j] = (int16_t)(compensate_gain * csi);
        }
    }
#else
    payload.len = info->len;
    int count = info->len;
    if (count > CSI_BINARY_MAX_LEN) {
        count = CSI_BINARY_MAX_LEN;
    }
    if (payload.len > CSI_BINARY_MAX_LEN) {
        payload.len = CSI_BINARY_MAX_LEN;
    }
    for (int i = 0; i < count; i++) {
        payload.csi[i] = (int16_t)(compensate_gain * info->buf[i]);
    }
#endif

#if CONFIG_WISENSE_CLASSIFIER_TINYML
    wisense_csi_infer_submit(payload.csi, payload.len, payload.local_timestamp, payload.id);
#endif

#if CONFIG_WISENSE_CSI_STREAM_BINARY
    csi_binary_header_t hdr = {
        .magic = CSI_BINARY_MAGIC,
        .version = CSI_BINARY_VERSION,
        .layout = CSI_BINARY_LAYOUT_C5C6,
        .seq = s_csi_frame_seq++,
        .payload_len = (uint16_t)sizeof(payload),
        .payload_crc16 = crc16_xmodem((const uint8_t *)&payload, sizeof(payload)),
    };

    csi_frame_enqueue(&hdr, sizeof(hdr), &payload, sizeof(payload));
#endif
}
#endif

static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf) {
        ESP_LOGW(TAG, "<%s> wifi_csi_cb", esp_err_to_name(ESP_ERR_INVALID_ARG));
        return;
    }

    if (memcmp(info->mac, CONFIG_CSI_SEND_MAC, 6)) {
        return;
    }

    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;
    static int s_count = 0;
    float compensate_gain = 1.0f;
    static uint8_t agc_gain = 0;
    static int8_t fft_gain = 0;
#if CONFIG_GAIN_CONTROL
    static uint8_t agc_gain_baseline = 0;
    static int8_t fft_gain_baseline = 0;
    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
    if (s_count < 100) {
        esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
    } else if (s_count == 100) {
        esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline, &fft_gain_baseline);
#if CONFIG_FORCE_GAIN
        esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
        ESP_LOGD(TAG, "fft_force %d, agc_force %d", fft_gain_baseline, agc_gain_baseline);
#endif
    }
    esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, agc_gain, fft_gain);
    ESP_LOGD(TAG, "compensate_gain %f, agc_gain %d, fft_gain %d", compensate_gain, agc_gain, fft_gain);
#endif

    /*
     * The sender's ESP-NOW payload is a uint32_t sequence counter.  In a CSI
     * callback, the 802.11 MAC header occupies the first 15 bytes of payload.
     * Copy rather than cast so this remains safe on targets that require
     * aligned uint32_t accesses.
     */
    uint32_t rx_id;
    memcpy(&rx_id, info->payload + 15, sizeof(rx_id));

    if (!s_count) {
        ESP_LOGI(TAG, "================ CSI RECV (binary) ================");
    }

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
    csi_binary_send_c5c6(rx_id, info, rx_ctrl, fft_gain, agc_gain, compensate_gain);
#else
    csi_binary_send_legacy(rx_id, info, rx_ctrl, compensate_gain);
#endif
    s_count++;
}

static void wifi_csi_init()
{
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

    /**< default config */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
    wifi_csi_config_t csi_config = {
        .enable                   = true,
        .acquire_csi_legacy       = false,
        .acquire_csi_force_lltf   = CSI_FORCE_LLTF,
        .acquire_csi_ht20         = true,
        .acquire_csi_ht40         = true,
        .acquire_csi_vht          = false,
        .acquire_csi_su           = false,
        .acquire_csi_mu           = false,
        .acquire_csi_dcm          = false,
        .acquire_csi_beamformed   = false,
        .acquire_csi_he_stbc_mode = 2,
        .val_scale_cfg            = 0,
        .dump_ack_en              = false,
        .reserved                 = false
    };
#elif CONFIG_IDF_TARGET_ESP32C6
    wifi_csi_config_t csi_config = {
        .enable                 = true,
        .acquire_csi_legacy     = false,
        .acquire_csi_ht20       = true,
        .acquire_csi_ht40       = true,
        .acquire_csi_su         = true,
        .acquire_csi_mu         = true,
        .acquire_csi_dcm        = true,
        .acquire_csi_beamformed = true,
        .acquire_csi_he_stbc    = 2,
        .val_scale_cfg          = false,
        .dump_ack_en            = false,
        .reserved               = false
    };
#else
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = true,
        .stbc_htltf2_en    = true,
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = false,
        .shift             = false,
    };
#endif
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

static void on_class_changed(wisense_class_t new_class, void *ctx)
{
    (void)ctx;

    bool skip_class_oled = false;
    ESP_ERROR_CHECK(emergency_rx_on_class_change(new_class, &skip_class_oled));

    if (!skip_class_oled) {
        esp_err_t err = wisense_oled_show_class(new_class);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "OLED update failed: %s", esp_err_to_name(err));
        }
    }

    esp_err_t err = wisense_light_on_class_change(new_class);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Light automation failed: %s", esp_err_to_name(err));
    }
}

void app_main()
{
    setvbuf(stdout, NULL, _IONBF, 0);

    /*
     * CRITICAL: the console driver's default TX line-ending mode
     * (ESP_LINE_ENDINGS_CRLF) inserts an extra 0x0D byte before every 0x0A
     * byte it writes — meant for human-readable log output, but it mangles
     * raw binary data whenever a byte happens to equal 0x0A. This was the
     * actual root cause of the CSI frame "loss" this session's live testing
     * found (confirmed via raw byte capture: header-valid frames' sequence
     * numbers were perfectly consecutive with zero gaps, but the on-wire
     * byte spacing between them was 818-819 bytes instead of the protocol's
     * fixed 817 — i.e. every frame really was arriving, just with 1-2 stray
     * bytes inserted, which desynced the fixed-size framing and failed
     * CRC). Must be set before the first CSI frame is ever written.
     */
    usb_serial_jtag_vfs_set_tx_line_endings(ESP_LINE_ENDINGS_LF);

    /*
     * Must be set up before anything logs: serializes every ESP_LOG* call
     * against the CSI writer task's raw stdout writes, and gives the CSI
     * callback a non-blocking queue instead of writing to the console
     * directly. See the comment above csi_frame_enqueue().
     */
    s_stdout_mutex = xSemaphoreCreateMutex();
    esp_log_set_vprintf(csi_console_vprintf);
#if CONFIG_WISENSE_CSI_STREAM_BINARY
    s_csi_frame_queue = xQueueCreate(CSI_FRAME_QUEUE_DEPTH, sizeof(csi_frame_msg_t));
    xTaskCreate(csi_uart_writer_task, "csi_uart_tx", 4096, NULL, tskIDLE_PRIORITY + 3, NULL);
#endif

    /**
     * @brief Initialize NVS
     */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    /**
     * @brief Initialize Wi-Fi
     */
    wifi_init();

    /**
     * @brief Initialize ESP-NOW
     *        ESP-NOW protocol see: https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_now.html
     */

    esp_now_peer_info_t peer = {
        .channel   = CONFIG_LESS_INTERFERENCE_CHANNEL,
        .ifidx     = WIFI_IF_STA,
        .encrypt   = false,
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };

    wifi_esp_now_init(peer);

    wifi_csi_init();

    /*
     * Hardware peripheral stack — OLED, relay/LDR, FSR, fall emergency
     * (servo + BLE phone trigger), placeholder classifier. Merged onto the
     * real ESP32-S3 RX target now rather than waiting for TinyML; see
     * docs/handoff.md for the two-board architecture (this board owns
     * every peripheral, the TX board only ever sends CSI).
     */
    ESP_ERROR_CHECK(wisense_oled_init(-1, -1));
    ESP_ERROR_CHECK(wisense_oled_show_class(WISENSE_CLASS_EMPTY));
    ESP_ERROR_CHECK(wisense_light_init(-1, -1));
    ESP_ERROR_CHECK(wisense_fsr_init(-1));
    ESP_ERROR_CHECK(emergency_rx_init());

    const wisense_classifier_ops_t *clf = wisense_classifier_get();
    ESP_ERROR_CHECK(clf->init());
    ESP_ERROR_CHECK(clf->set_on_change(on_class_changed, NULL));
    ESP_ERROR_CHECK(clf->start());

#if CONFIG_WISENSE_CLASSIFIER_TINYML
    ESP_LOGI(TAG, "Hardware peripherals ready. Classifying from live CSI on-device.");
#else
    ESP_LOGI(TAG, "Hardware peripherals ready. Type e/p/m/f in the serial monitor to change class.");
#endif
}
