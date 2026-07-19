/*
 * WiSense hardware bring-up — Modules 1–5
 *
 * OLED (SSD1306) displays the current prediction class.
 * Placeholder classifier accepts UART keys:
 *   e -> Empty
 *   p -> Presence
 *   m -> Motion
 *   f -> Fall
 *
 * Module 2: relay + LDR light automation.
 * Module 3: FSR406 pressure sensor (bed / sleep detection).
 * Module 4+5: cancel button + fall emergency (see emergency_rx.c).
 */
#include "esp_log.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "wisense_classifier.h"
#include "wisense_fsr.h"
#include "wisense_light.h"
#include "wisense_oled.h"

#include "emergency_rx.h"

static const char *TAG = "wisense_hw";

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

void app_main(void)
{
    ESP_LOGI(TAG, "Modules 1–5: OLED + classifier + light + FSR + emergency");

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(wisense_oled_init(CONFIG_WISENSE_OLED_I2C_SDA_GPIO,
                                      CONFIG_WISENSE_OLED_I2C_SCL_GPIO));
    ESP_ERROR_CHECK(wisense_oled_show_class(WISENSE_CLASS_EMPTY));

    ESP_ERROR_CHECK(wisense_light_init(-1, -1));
    ESP_ERROR_CHECK(wisense_fsr_init(-1));
    ESP_ERROR_CHECK(emergency_rx_init());

    const wisense_classifier_ops_t *clf = wisense_classifier_get();
    ESP_ERROR_CHECK(clf->init());
    ESP_ERROR_CHECK(clf->set_on_change(on_class_changed, NULL));
    ESP_ERROR_CHECK(clf->start());

    ESP_LOGI(TAG, "Ready. Type e/p/m/f in the serial monitor to change class.");
    ESP_LOGI(TAG, "Module 4+5: type f for fall emergency; press GPIO27 button to cancel.");
}
