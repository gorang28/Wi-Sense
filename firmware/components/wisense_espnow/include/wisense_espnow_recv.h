/*
 * ESP-NOW emergency receiver API (TX / ESP32-CAM).
 */
#pragma once

#include "esp_err.h"
#include "wisense_espnow_proto.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*wisense_espnow_emerg_cb_t)(wisense_espnow_msg_type_t type, void *ctx);

/**
 * @brief Register callback for validated emergency packets.
 *
 * Call after esp_now_init(). CSI counter frames (4 bytes) are ignored.
 */
esp_err_t wisense_espnow_emerg_receiver_register(wisense_espnow_emerg_cb_t cb, void *ctx);

#ifdef __cplusplus
}
#endif
