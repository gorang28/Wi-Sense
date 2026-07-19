/*
 * ESP-NOW emergency receiver (TX / ESP32-CAM).
 *
 * Registers an ESP-NOW recv callback; ignores non-emergency frames (CSI counter).
 */
#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_now.h"

#include "wisense_espnow_proto.h"
#include "wisense_espnow_recv.h"

static const char *TAG = "wisense_espnow_rx";

static wisense_espnow_emerg_cb_t s_cb;
static void *s_cb_ctx;

static void espnow_recv_cb(const esp_now_recv_info_t *info, const uint8_t *data, int len)
{
    (void)info;

    wisense_espnow_emerg_pkt_t pkt;
    if (!wisense_espnow_emerg_parse(data, len, &pkt)) {
        return;
    }

    ESP_LOGI(TAG, "Emergency ESP-NOW from " MACSTR " type=%u seq=%u",
             MAC2STR(info->src_addr), (unsigned)pkt.type, (unsigned)pkt.seq);

    if (s_cb != NULL) {
        s_cb((wisense_espnow_msg_type_t)pkt.type, s_cb_ctx);
    }
}

esp_err_t wisense_espnow_emerg_receiver_register(wisense_espnow_emerg_cb_t cb, void *ctx)
{
    s_cb = cb;
    s_cb_ctx = ctx;

    ESP_RETURN_ON_ERROR(esp_now_register_recv_cb(espnow_recv_cb), TAG, "register recv cb");

    ESP_LOGI(TAG, "Emergency ESP-NOW receiver registered");
    return ESP_OK;
}
