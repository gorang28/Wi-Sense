/*
 * ESP-NOW emergency sender API (RX board).
 */
#pragma once

#include "esp_err.h"
#include "wisense_espnow_proto.h"

#ifdef __cplusplus
extern "C" {
#endif

/** One-time Wi-Fi + ESP-NOW setup for emergency TX packets to ESP32-CAM. */
esp_err_t wisense_espnow_emerg_sender_init(void);

/** Send a fall-alert or cancel packet to the configured TX MAC. */
esp_err_t wisense_espnow_emerg_send(wisense_espnow_msg_type_t type);

#ifdef __cplusplus
}
#endif
