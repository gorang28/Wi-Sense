/*
 * RX → TX emergency notification via ESP-NOW (Module 7).
 */
#include "esp_log.h"

#include "wisense_espnow_proto.h"
#include "wisense_espnow_send.h"
#include "wisense_emergency_internal.h"

static const char *TAG = "wisense_emerg_notify";

void wisense_emergency_notify_tx(void)
{
    esp_err_t err = wisense_espnow_emerg_send(WISENSE_ESPNOW_MSG_FALL_ALERT);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to send fall alert: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "Fall alert sent to TX via ESP-NOW");
    }
}

void wisense_emergency_notify_tx_cancel(void)
{
    esp_err_t err = wisense_espnow_emerg_send(WISENSE_ESPNOW_MSG_FALL_CANCEL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to send fall cancel: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "Fall cancel sent to TX via ESP-NOW");
    }
}
