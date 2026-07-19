/*
 * WiSense ESP-NOW emergency packet (RX → TX).
 *
 * Distinct from the 4-byte CSI counter (uint32_t) sent TX → RX for CSI capture.
 * TX must check magic before treating a frame as emergency control traffic.
 */
#pragma once

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Magic — not produced by the monotonic 4-byte CSI counter at boot. */
#define WISENSE_ESPNOW_EMERG_MAGIC  0xE911C1A5u

typedef enum {
    WISENSE_ESPNOW_MSG_FALL_ALERT  = 1,
    WISENSE_ESPNOW_MSG_FALL_CANCEL = 2,
} wisense_espnow_msg_type_t;

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint8_t  type;
    uint8_t  seq;
    uint16_t reserved;
} wisense_espnow_emerg_pkt_t;

#define WISENSE_ESPNOW_EMERG_PKT_LEN  (sizeof(wisense_espnow_emerg_pkt_t))

/** Parse and validate an ESP-NOW payload. Returns false if not an emergency packet. */
bool wisense_espnow_emerg_parse(const uint8_t *data, int len, wisense_espnow_emerg_pkt_t *out);

/** Build a fall-alert or cancel packet into @p out (seq incremented internally on send). */
void wisense_espnow_emerg_build(wisense_espnow_msg_type_t type, uint8_t seq,
                                wisense_espnow_emerg_pkt_t *out);

#ifdef __cplusplus
}
#endif
