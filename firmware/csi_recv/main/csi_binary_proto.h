/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CSI_BINARY_MAGIC            0xC511
#define CSI_BINARY_VERSION          2
#define CSI_BINARY_LAYOUT_LEGACY    0
#define CSI_BINARY_LAYOUT_C5C6      1
#define CSI_BINARY_MAX_LEN          384

/*
 * Version 2: adds an RX-local monotonic frame sequence number and a
 * CRC16/XMODEM over the payload bytes, plus a self-describing payload
 * length. None of magic/layout alone can detect corruption *inside* a
 * frame (bad RSSI, timestamp, or CSI samples) or a frame silently dropped
 * between the CSI callback and the UART writer (e.g. queue full under
 * load) — seq lets the host detect the latter, crc16 the former.
 */
typedef struct __attribute__((packed)) {
    uint16_t magic;
    uint8_t version;
    uint8_t layout;
    uint32_t seq;
    uint16_t payload_len;
    uint16_t payload_crc16;
} csi_binary_header_t;

typedef struct __attribute__((packed)) {
    uint32_t id;
    uint8_t mac[6];
    int8_t rssi;
    uint8_t rate;
    uint8_t sig_mode;
    uint8_t mcs;
    uint8_t bandwidth;
    uint8_t smoothing;
    uint8_t not_sounding;
    uint8_t aggregation;
    uint8_t stbc;
    uint8_t fec_coding;
    uint8_t sgi;
    int8_t noise_floor;
    uint8_t ampdu_cnt;
    uint8_t channel;
    uint8_t secondary_channel;
    uint32_t local_timestamp;
    uint8_t ant;
    uint16_t sig_len;    // widened from uint8_t: hardware field is 12 bits (0-4095), was wrapping mod 256
    uint8_t rx_format;
    uint16_t len;
    uint8_t first_word_invalid;
    uint8_t reserved;
    int16_t csi[CSI_BINARY_MAX_LEN];
} csi_binary_legacy_payload_t;

typedef struct __attribute__((packed)) {
    uint32_t id;
    uint8_t mac[6];
    int8_t rssi;
    uint8_t rate;
    int8_t noise_floor;
    int8_t fft_gain;
    int8_t agc_gain;
    uint8_t channel;
    uint32_t local_timestamp;
    uint16_t sig_len;    // widened from uint8_t, see above
    uint8_t rx_format;
    uint16_t len;
    uint8_t first_word_invalid;
    uint8_t reserved;
    int16_t csi[CSI_BINARY_MAX_LEN];
} csi_binary_c5c6_payload_t;

#ifdef __cplusplus
}
#endif
