/*
 * RX -> phone emergency trigger via BLE (replaces the old RX -> ESP32-CAM
 * ESP-NOW notify now that RX drives the phone and servo itself).
 */
#include "esp_log.h"

#include "wisense_ble_trigger.h"
#include "wisense_emergency_internal.h"

static const char *TAG = "wisense_emerg_notify";

void wisense_emergency_notify_tx(void)
{
    esp_err_t err = wisense_ble_trigger_send(WISENSE_BLE_TRIGGER_ALERT);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to send fall alert: %s", esp_err_to_name(err));
    }
}

void wisense_emergency_notify_tx_cancel(void)
{
    esp_err_t err = wisense_ble_trigger_send(WISENSE_BLE_TRIGGER_CANCEL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to send fall cancel: %s", esp_err_to_name(err));
    }
}
