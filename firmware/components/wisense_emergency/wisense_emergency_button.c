/*
 * Cancel-button input for RX fall emergency.
 */
#include "driver/gpio.h"
#include "esp_check.h"
#include "sdkconfig.h"

#include "wisense_emergency_internal.h"

static int s_button_gpio = -1;
static bool s_ready;

esp_err_t wisense_emergency_button_init(int gpio)
{
    if (gpio < 0) {
        gpio = CONFIG_WISENSE_EMERGENCY_BUTTON_GPIO;
    }

    gpio_config_t btn_cfg = {
        .pin_bit_mask = (1ULL << gpio),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&btn_cfg), "wisense_emerg_btn", "gpio_config");

    s_button_gpio = gpio;
    s_ready = true;
    return ESP_OK;
}

bool wisense_emergency_button_is_pressed(void)
{
    if (!s_ready) {
        return false;
    }

    int level = gpio_get_level(s_button_gpio);
#if CONFIG_WISENSE_EMERGENCY_BUTTON_ACTIVE_LOW
    return level == 0;
#else
    return level != 0;
#endif
}
