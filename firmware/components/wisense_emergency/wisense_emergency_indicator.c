/*
 * Emergency indicator LED — mirrors buzzer ON/OFF in sync with beep patterns.
 */
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#include "sdkconfig.h"

#include "wisense_emergency_internal.h"

static const char *TAG = "wisense_emerg_led";

static int s_gpio = -1;
static bool s_ready;

static int led_on_level(void)
{
#if CONFIG_WISENSE_EMERGENCY_LED_ON_HIGH
    return 1;
#else
    return 0;
#endif
}

static int led_off_level(void)
{
    return led_on_level() ? 0 : 1;
}

esp_err_t wisense_emergency_indicator_init(int gpio)
{
    if (s_ready) {
        return ESP_OK;
    }

    if (gpio < 0) {
        gpio = CONFIG_WISENSE_EMERGENCY_LED_GPIO;
    }

    gpio_config_t cfg = {
        .pin_bit_mask = (1ULL << gpio),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&cfg), TAG, "gpio");

    s_gpio = gpio;
    gpio_set_level(s_gpio, led_off_level());
    s_ready = true;

    ESP_LOGI(TAG, "Emergency LED ready (GPIO%d on=%s)",
             s_gpio, led_on_level() ? "HIGH" : "LOW");
    return ESP_OK;
}

void wisense_emergency_indicator_set(bool on)
{
    if (!s_ready) {
        return;
    }
    gpio_set_level(s_gpio, on ? led_on_level() : led_off_level());
}

void wisense_emergency_indicator_stop(void)
{
    wisense_emergency_indicator_set(false);
}
