/*
 * Fall emergency: 15 s OLED countdown + cancel button (Modules 4+5).
 */
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "wisense_classifier.h"
#include "wisense_emergency.h"
#include "wisense_oled.h"

static const char *TAG = "wisense_emergency";

typedef enum {
    EMERGENCY_IDLE = 0,
    EMERGENCY_COUNTDOWN,
    EMERGENCY_TRIGGERED,
} emergency_state_t;

static int s_button_gpio = -1;
static bool s_ready;
static emergency_state_t s_state = EMERGENCY_IDLE;
static int s_countdown_sec;
static wisense_class_t s_display_class = WISENSE_CLASS_EMPTY;
static esp_timer_handle_t s_countdown_timer;

static bool button_is_pressed(void)
{
    int level = gpio_get_level(s_button_gpio);
#if CONFIG_WISENSE_EMERGENCY_BUTTON_ACTIVE_LOW
    return level == 0;
#else
    return level != 0;
#endif
}

static void restore_class_display(void)
{
    (void)wisense_oled_show_class(s_display_class);
}

static void notify_tx_stub(void)
{
    /* Module 7: ESP-NOW emergency packet to TX — stub for now. */
    ESP_LOGW(TAG, "Emergency countdown finished — ESP-NOW notify TX (not implemented yet)");
}

static void cancel_emergency(const char *reason)
{
    ESP_LOGI(TAG, "Emergency cancelled (%s)", reason);
    esp_timer_stop(s_countdown_timer);
    s_state = EMERGENCY_IDLE;
    restore_class_display();
}

static void finish_emergency(void)
{
    esp_timer_stop(s_countdown_timer);
    s_state = EMERGENCY_TRIGGERED;
    notify_tx_stub();
    (void)wisense_oled_show_emergency(true, 0);
    ESP_LOGI(TAG, "Emergency triggered — TX notification pending (Module 7)");
}

static void countdown_timer_cb(void *arg)
{
    (void)arg;

    if (s_state != EMERGENCY_COUNTDOWN) {
        return;
    }

    if (button_is_pressed()) {
        cancel_emergency("button");
        return;
    }

    s_countdown_sec--;
    if (s_countdown_sec <= 0) {
        finish_emergency();
        return;
    }

    (void)wisense_oled_show_emergency(true, s_countdown_sec);
    ESP_LOGI(TAG, "Emergency countdown: %d s remaining (press button to cancel)", s_countdown_sec);
}

static void start_countdown(wisense_class_t cls)
{
    s_display_class = cls;
    s_countdown_sec = CONFIG_WISENSE_EMERGENCY_COUNTDOWN_SEC;
    s_state = EMERGENCY_COUNTDOWN;

    (void)wisense_oled_show_emergency(true, s_countdown_sec);
    ESP_LOGI(TAG, "Fall detected — emergency countdown %d s (press button to cancel)",
             s_countdown_sec);

    esp_timer_stop(s_countdown_timer);
    esp_timer_start_periodic(s_countdown_timer, 1000000ULL);
}

esp_err_t wisense_emergency_init(int button_gpio)
{
    if (s_ready) {
        return ESP_OK;
    }

    if (button_gpio < 0) {
        button_gpio = CONFIG_WISENSE_EMERGENCY_BUTTON_GPIO;
    }

    gpio_config_t btn_cfg = {
        .pin_bit_mask = (1ULL << button_gpio),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&btn_cfg), TAG, "button gpio");

    const esp_timer_create_args_t timer_args = {
        .callback = countdown_timer_cb,
        .name = "emerg_cd",
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&timer_args, &s_countdown_timer), TAG, "timer");

    s_button_gpio = button_gpio;
    s_state = EMERGENCY_IDLE;
    s_ready = true;

    ESP_LOGI(TAG, "Emergency ready (button=GPIO%d pressed=%s, countdown=%ds)",
             s_button_gpio,
#if CONFIG_WISENSE_EMERGENCY_BUTTON_ACTIVE_LOW
             "LOW",
#else
             "HIGH",
#endif
             CONFIG_WISENSE_EMERGENCY_COUNTDOWN_SEC);
    return ESP_OK;
}

bool wisense_emergency_is_active(void)
{
    return s_state != EMERGENCY_IDLE;
}

esp_err_t wisense_emergency_on_class_change(wisense_class_t new_class)
{
    ESP_RETURN_ON_FALSE(s_ready, ESP_ERR_INVALID_STATE, TAG, "not init");

    if (s_state == EMERGENCY_COUNTDOWN) {
        if (new_class != WISENSE_CLASS_FALL) {
            cancel_emergency("class changed");
        }
        return ESP_OK;
    }

    if (s_state == EMERGENCY_TRIGGERED) {
        if (new_class != WISENSE_CLASS_FALL) {
            s_state = EMERGENCY_IDLE;
            (void)wisense_oled_show_class(new_class);
        }
        return ESP_OK;
    }

    if (new_class == WISENSE_CLASS_FALL) {
        start_countdown(new_class);
    }

    return ESP_OK;
}
