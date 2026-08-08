/*
 * Emergency buzzer patterns (RX):
 *   Countdown — fast short beeps for the full 15 s (button cancels).
 *   Alert     — buzzer held ON continuously (cam / TX emergency active).
 */
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "wisense_emergency_internal.h"

static const char *TAG = "wisense_emerg_buzz";

typedef enum {
    BUZZER_OFF = 0,
    BUZZER_COUNTDOWN,
    BUZZER_ALERT,
} buzzer_mode_t;

static int s_gpio = -1;
static bool s_ready;
static buzzer_mode_t s_mode = BUZZER_OFF;
static esp_timer_handle_t s_fast_beep_timer;
static esp_timer_handle_t s_pulse_off_timer;

static int buzzer_on_level(void)
{
#if CONFIG_WISENSE_EMERGENCY_BUZZER_ON_HIGH
    return 1;
#else
    return 0;
#endif
}

static int buzzer_off_level(void)
{
    return buzzer_on_level() ? 0 : 1;
}

static void buzzer_gpio_set(bool on)
{
    if (!s_ready) {
        return;
    }
    gpio_set_level(s_gpio, on ? buzzer_on_level() : buzzer_off_level());
    wisense_emergency_indicator_set(on);
}

static void pulse_off_cb(void *arg)
{
    (void)arg;
    if (s_mode == BUZZER_COUNTDOWN) {
        buzzer_gpio_set(false);
    }
}

static void emit_short_beep(void)
{
    buzzer_gpio_set(true);
    esp_timer_stop(s_pulse_off_timer);
    esp_timer_start_once(s_pulse_off_timer,
                         (uint64_t)CONFIG_WISENSE_EMERGENCY_BUZZER_PULSE_MS * 1000ULL);
}

static void fast_beep_timer_cb(void *arg)
{
    (void)arg;

    if (s_mode != BUZZER_COUNTDOWN) {
        return;
    }

    emit_short_beep();
}

static void stop_timers(void)
{
    if (s_fast_beep_timer != NULL) {
        esp_timer_stop(s_fast_beep_timer);
    }
    if (s_pulse_off_timer != NULL) {
        esp_timer_stop(s_pulse_off_timer);
    }
}

esp_err_t wisense_emergency_buzzer_init(int gpio)
{
    if (s_ready) {
        return ESP_OK;
    }

    if (gpio < 0) {
        gpio = CONFIG_WISENSE_EMERGENCY_BUZZER_GPIO;
    }

    gpio_config_t cfg = {
        .pin_bit_mask = (1ULL << gpio),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&cfg), TAG, "gpio");

    const esp_timer_create_args_t fast_args = {
        .callback = fast_beep_timer_cb,
        .name = "buzz_fast",
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&fast_args, &s_fast_beep_timer), TAG, "fast timer");

    const esp_timer_create_args_t pulse_args = {
        .callback = pulse_off_cb,
        .name = "buzz_off",
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&pulse_args, &s_pulse_off_timer), TAG, "pulse timer");

    s_gpio = gpio;
    s_mode = BUZZER_OFF;
    buzzer_gpio_set(false);
    s_ready = true;

    ESP_LOGI(TAG, "Buzzer ready (GPIO%d on=%s, fast=%dms, pulse=%dms)",
             s_gpio,
             buzzer_on_level() ? "HIGH" : "LOW",
             CONFIG_WISENSE_EMERGENCY_BUZZER_FAST_INTERVAL_MS,
             CONFIG_WISENSE_EMERGENCY_BUZZER_PULSE_MS);
    return ESP_OK;
}

void wisense_emergency_buzzer_stop(void)
{
    if (!s_ready) {
        return;
    }
    stop_timers();
    buzzer_gpio_set(false);
    s_mode = BUZZER_OFF;
    wisense_emergency_indicator_stop();
}

void wisense_emergency_buzzer_start_countdown(void)
{
    if (!s_ready) {
        return;
    }
    stop_timers();
    buzzer_gpio_set(false);
    s_mode = BUZZER_COUNTDOWN;

    emit_short_beep();
    esp_timer_start_periodic(s_fast_beep_timer,
                             (uint64_t)CONFIG_WISENSE_EMERGENCY_BUZZER_FAST_INTERVAL_MS * 1000ULL);
    ESP_LOGI(TAG, "Fast countdown beeps (%d ms interval)", CONFIG_WISENSE_EMERGENCY_BUZZER_FAST_INTERVAL_MS);
}

void wisense_emergency_buzzer_start_alert(void)
{
    if (!s_ready) {
        return;
    }
    stop_timers();
    s_mode = BUZZER_ALERT;
    buzzer_gpio_set(true);
    ESP_LOGI(TAG, "Alert mode — buzzer ON continuously");
}
