/*
 * RX fall-emergency state machine (Modules 4+5).
 *
 * Fall class -> 15 s OLED countdown -> alert (servo + BLE phone trigger).
 * Once a countdown has started, only the physical cancel button can stop
 * it (during the countdown or after the alert has fired) — a class-stream
 * change is ignored so a noisy live classifier can't silently cancel a
 * real emergency the way the old keyboard-placeholder-only design allowed.
 *
 * s_state / s_countdown_sec / s_display_class are touched from more than
 * one FreeRTOS task (the classifier's on-change callback vs. esp_timer
 * callbacks), so every entry point below takes s_mutex for its whole body.
 * Internal helpers (cancel_emergency, finish_emergency, start_countdown,
 * start/stop_button_poll, restore_class_display) assume the caller already
 * holds the lock — they do not take it themselves.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "wisense_ble_trigger.h"
#include "wisense_classifier.h"
#include "wisense_emergency.h"
#include "wisense_emergency_internal.h"
#include "wisense_light.h"
#include "wisense_oled.h"
#include "wisense_servo.h"

static const char *TAG = "wisense_emergency_rx";

typedef enum {
    EMERGENCY_IDLE = 0,
    EMERGENCY_COUNTDOWN,
    EMERGENCY_TRIGGERED,
} emergency_state_t;

static bool s_ready;
static SemaphoreHandle_t s_mutex;
static emergency_state_t s_state = EMERGENCY_IDLE;
static int s_countdown_sec;
/* Last non-Fall class seen while IDLE — what the room actually was doing
 * before Fall interrupted it. Restored to OLED + light automation on
 * cancellation instead of leaving both stuck showing/reacting to Fall. */
static wisense_class_t s_last_normal_class = WISENSE_CLASS_EMPTY;
static esp_timer_handle_t s_countdown_timer;
static esp_timer_handle_t s_button_poll_timer;

/* Cancellation must undo everything the Fall transition changed: the OLED
 * (which was owned by the countdown/alert screen) and wisense_light (which
 * stopped LDR polling and froze relay state when it saw class=Fall) both
 * need to be told what the room actually is now, not just returned to IDLE. */
static void restore_normal_state(void)
{
    (void)wisense_oled_show_class(s_last_normal_class);
    (void)wisense_light_on_class_change(s_last_normal_class);
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
        wisense_servo_close();
    }

    s_state = EMERGENCY_IDLE;
    restore_normal_state();
}

/* Runs every 100 ms during both COUNTDOWN and TRIGGERED — the only way to
 * cancel a started emergency once a countdown has begun. */
static void button_poll_cb(void *arg)
{
    (void)arg;

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    if ((s_state == EMERGENCY_COUNTDOWN || s_state == EMERGENCY_TRIGGERED) &&
        wisense_emergency_button_is_pressed()) {
        cancel_emergency("button");
    }
    xSemaphoreGive(s_mutex);
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
    wisense_servo_open();
    (void)wisense_oled_show_emergency(true, 0);
    start_button_poll();
    ESP_LOGI(TAG, "Emergency active — press button to cancel");
}

static void countdown_timer_cb(void *arg)
{
    (void)arg;

    xSemaphoreTake(s_mutex, portMAX_DELAY);

    if (s_state != EMERGENCY_COUNTDOWN) {
        xSemaphoreGive(s_mutex);
        return;
    }

    s_countdown_sec--;
    if (s_countdown_sec <= 0) {
        finish_emergency();
        xSemaphoreGive(s_mutex);
        return;
    }

    (void)wisense_oled_show_emergency(true, s_countdown_sec);
    ESP_LOGI(TAG, "Countdown: %d s remaining (press button to cancel)", s_countdown_sec);
    xSemaphoreGive(s_mutex);
}

/* Button polling starts here (not just at finish_emergency) so cancel
 * latency is a consistent ~100 ms in COUNTDOWN and TRIGGERED alike. */
static void start_countdown(void)
{
    s_countdown_sec = CONFIG_WISENSE_EMERGENCY_COUNTDOWN_SEC;
    s_state = EMERGENCY_COUNTDOWN;

    (void)wisense_oled_show_emergency(true, s_countdown_sec);
    ESP_LOGI(TAG, "Fall detected — %d s countdown (press button to cancel)", s_countdown_sec);

    wisense_emergency_buzzer_start_countdown();
    start_button_poll();

    esp_timer_stop(s_countdown_timer);
    esp_timer_start_periodic(s_countdown_timer, 1000000ULL);
}

esp_err_t wisense_emergency_init(int button_gpio)
{
    if (s_ready) {
        return ESP_OK;
    }

    s_mutex = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_mutex != NULL, ESP_ERR_NO_MEM, TAG, "mutex");

    ESP_RETURN_ON_ERROR(wisense_emergency_button_init(button_gpio), TAG, "button");
    ESP_RETURN_ON_ERROR(wisense_emergency_buzzer_init(-1), TAG, "buzzer");
    ESP_RETURN_ON_ERROR(wisense_emergency_indicator_init(-1), TAG, "led");
    ESP_RETURN_ON_ERROR(wisense_servo_init(-1), TAG, "servo");
    ESP_RETURN_ON_ERROR(wisense_ble_trigger_init(), TAG, "ble trigger");

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
    if (s_mutex == NULL) {
        return false;
    }

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    bool active = (s_state != EMERGENCY_IDLE);
    xSemaphoreGive(s_mutex);
    return active;
}

esp_err_t wisense_emergency_on_class_change(wisense_class_t new_class)
{
    ESP_RETURN_ON_FALSE(s_ready, ESP_ERR_INVALID_STATE, TAG, "not init");

    xSemaphoreTake(s_mutex, portMAX_DELAY);

    if (s_state == EMERGENCY_COUNTDOWN || s_state == EMERGENCY_TRIGGERED) {
        /*
         * Once a countdown has started, only the physical button (see
         * button_poll_cb) can cancel it. A class-stream change here — a
         * stray keypress today, a noisy live-ML misclassification later —
         * must not silently abort a real emergency.
         */
        xSemaphoreGive(s_mutex);
        return ESP_OK;
    }

    if (new_class == WISENSE_CLASS_FALL) {
        start_countdown();
    } else {
        s_last_normal_class = new_class;
    }

    xSemaphoreGive(s_mutex);
    return ESP_OK;
}
