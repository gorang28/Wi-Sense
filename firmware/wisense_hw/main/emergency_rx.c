/*
 * RX emergency app glue — keeps app_main focused on bring-up only.
 */
#include "esp_check.h"
#include "esp_log.h"

#include "wisense_emergency.h"

#include "emergency_rx.h"

static const char *TAG = "emergency_rx";

esp_err_t emergency_rx_init(void)
{
    esp_err_t err = wisense_emergency_init(-1);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Emergency init failed: %s", esp_err_to_name(err));
    }
    return err;
}

esp_err_t emergency_rx_on_class_change(wisense_class_t new_class, bool *skip_class_oled)
{
    ESP_RETURN_ON_ERROR(wisense_emergency_on_class_change(new_class), TAG, "on_class_change");

    if (skip_class_oled != NULL) {
        *skip_class_oled = wisense_emergency_is_active();
    }
    return ESP_OK;
}
