/*
 * RX fall-emergency state machine (Modules 4+5).
 *
 * Fall class → 15 s OLED countdown → cancel button or notify TX.
 * Button cancels during countdown and after alert is active.
 */
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "wisense_classifier.h"
#include "wisense_emergency.h"
#include "wisense_emergency_internal.h"
#include "wisense_oled.h"
#include "wisense_espnow_send.h"

static const char *TAG = "wisense_emergency_rx";

typedef enum {
    EMERGENCY_IDLE = 0,
    EMERGENCY_COUNTDOWN,
    EMERGENCY_TRIGGERED,
} emergency_state_t;

static bool s_ready;
static emergency_state_t s_state = EMERGENCY_IDLE;
static int s_countdown_sec;
static wisense_class_t s_display_class = WISENSE_CLASS_EMPTY;
static esp_timer_handle_t s_countdown_timer;
static esp_timer_handle_t s_button_poll_timer;

static void restore_class_display(void)
{
    (void)wisense_oled_show_class(s_display_class);
}

static void stop_button_poll(void)
{
    if (s_button_poll_timer != NULL) {
        esp_timer_stop(s_button_poll_timer);
    }
}

static void cancel_emergency(const char *reason)
{
    const bool was_triggered = (s_state == EMERGENCY_TRIGGERED);

    ESP_LOGI(TAG, "Emergency cancelled (%s)", reason);
    esp_timer_stop(s_countdown_timer);
    stop_button_poll();
    wisense_emergency_buzzer_stop();
    wisense_emergency_indicator_stop();

    if (was_triggered) {
        wisense_emergency_notify_tx_cancel();
    }

    s_state = EMERGENCY_IDLE;
    restore_class_display();
}

static void button_poll_cb(void *arg)
{
    (void)arg;

    if (s_state != EMERGENCY_TRIGGERED) {
        return;
    }

    if (wisense_emergency_button_is_pressed()) {
        cancel_emergency("button");
    }
}

static void start_button_poll(void)
{
    stop_button_poll();
    esp_timer_start_periodic(s_button_poll_timer, 100000ULL);
}

static void finish_emergency(void)
{
    esp_timer_stop(s_countdown_timer);
    s_state = EMERGENCY_TRIGGERED;
    wisense_emergency_buzzer_stop();
    wisense_emergency_buzzer_start_alert();
    wisense_emergency_notify_tx();
    (void)wisense_oled_show_emergency(true, 0);
    start_button_poll();
    ESP_LOGI(TAG, "Emergency active — press button to cancel");
}

static void countdown_timer_cb(void *arg)
{
    (void)arg;

    if (s_state != EMERGENCY_COUNTDOWN) {
        return;
    }

    if (wisense_emergency_button_is_pressed()) {
        cancel_emergency("button");
        return;
    }

    s_countdown_sec--;
    if (s_countdown_sec <= 0) {
        finish_emergency();
        return;
    }

    (void)wisense_oled_show_emergency(true, s_countdown_sec);
    ESP_LOGI(TAG, "Countdown: %d s remaining (press button to cancel)", s_countdown_sec);
}

static void start_countdown(wisense_class_t cls)
{
    s_display_class = cls;
    s_countdown_sec = CONFIG_WISENSE_EMERGENCY_COUNTDOWN_SEC;
    s_state = EMERGENCY_COUNTDOWN;

    (void)wisense_oled_show_emergency(true, s_countdown_sec);
    ESP_LOGI(TAG, "Fall detected — %d s countdown (press button to cancel)", s_countdown_sec);

    wisense_emergency_buzzer_start_countdown();

    esp_timer_stop(s_countdown_timer);
    esp_timer_start_periodic(s_countdown_timer, 1000000ULL);
}

esp_err_t wisense_emergency_init(int button_gpio)
{
    if (s_ready) {
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(wisense_emergency_button_init(button_gpio), TAG, "button");
    ESP_RETURN_ON_ERROR(wisense_emergency_buzzer_init(-1), TAG, "buzzer");
    ESP_RETURN_ON_ERROR(wisense_emergency_indicator_init(-1), TAG, "led");
    ESP_RETURN_ON_ERROR(wisense_espnow_emerg_sender_init(), TAG, "espnow");

    const esp_timer_create_args_t countdown_args = {
        .callback = countdown_timer_cb,
        .name = "emerg_cd",
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&countdown_args, &s_countdown_timer), TAG, "countdown timer");

    const esp_timer_create_args_t poll_args = {
        .callback = button_poll_cb,
        .name = "emerg_btn",
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&poll_args, &s_button_poll_timer), TAG, "button poll");

    s_state = EMERGENCY_IDLE;
    s_ready = true;

    ESP_LOGI(TAG, "RX emergency ready (countdown=%ds)", CONFIG_WISENSE_EMERGENCY_COUNTDOWN_SEC);
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
            stop_button_poll();
            wisense_emergency_notify_tx_cancel();
            s_state = EMERGENCY_IDLE;
            wisense_emergency_buzzer_stop();
            wisense_emergency_indicator_stop();
            (void)wisense_oled_show_class(new_class);
        }
        return ESP_OK;
    }

    if (new_class == WISENSE_CLASS_FALL) {
        start_countdown(new_class);
    }

    return ESP_OK;
}
