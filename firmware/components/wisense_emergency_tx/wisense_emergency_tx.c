/*
 * TX fall-emergency orchestrator state machine (TX-1).
 *
 * States: IDLE → ALERT → RUNNING → (CANCELLED) → IDLE
 * ESP-NOW callbacks only enqueue events; this task runs the workflow.
 */
#include <stdbool.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "esp_check.h"
#include "esp_log.h"
#include "sdkconfig.h"

#include "wisense_emergency_tx.h"
#include "wisense_emergency_tx_internal.h"

static const char *TAG = "wisense_emerg_tx";

typedef enum {
    EMERG_TX_IDLE = 0,
    EMERG_TX_ALERT,
    EMERG_TX_RUNNING,
    EMERG_TX_CANCELLED,
} emerg_tx_state_t;

typedef enum {
    EVT_FALL_ALERT = 0,
    EVT_FALL_CANCEL,
} emerg_tx_evt_t;

static QueueHandle_t s_evt_queue;
static TaskHandle_t s_worker_task;
static emerg_tx_state_t s_state = EMERG_TX_IDLE;
static volatile bool s_abort;

static const char *state_name(emerg_tx_state_t state)
{
    switch (state) {
    case EMERG_TX_IDLE:
        return "IDLE";
    case EMERG_TX_ALERT:
        return "ALERT";
    case EMERG_TX_RUNNING:
        return "RUNNING";
    case EMERG_TX_CANCELLED:
        return "CANCELLED";
    default:
        return "?";
    }
}

static void set_state(emerg_tx_state_t state)
{
    if (s_state == state) {
        return;
    }

    ESP_LOGI(TAG, "state %s → %s", state_name(s_state), state_name(state));
    s_state = state;
}

static bool wait_ms_abortable(int ms)
{
    if (ms <= 0) {
        return s_abort;
    }

    const TickType_t slice = pdMS_TO_TICKS(50);
    int remaining = ms;

    while (remaining > 0 && !s_abort) {
        const int step = remaining > 50 ? 50 : remaining;
        vTaskDelay(pdMS_TO_TICKS(step));
        remaining -= step;
    }

    return s_abort;
}

static esp_err_t run_stage(esp_err_t (*stage_fn)(void), const char *label)
{
    ESP_LOGI(TAG, "stage: %s", label);
    const esp_err_t err = stage_fn();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "stage '%s' failed: %s", label, esp_err_to_name(err));
    }
    return err;
}

static void shutdown_sequence(void)
{
    set_state(EMERG_TX_CANCELLED);

    (void)run_stage(wisense_emergency_tx_stage_stream_stop, "stream stop");
    (void)run_stage(wisense_emergency_tx_stage_mic_stop, "mic stop");
    (void)run_stage(wisense_emergency_tx_stage_camera_stop, "camera stop");
    (void)run_stage(wisense_emergency_tx_stage_servo_close, "servo close");

    set_state(EMERG_TX_IDLE);
    ESP_LOGI(TAG, "emergency cleared — CSI send continues");
}

static void handle_fall_alert(void)
{
    if (s_state != EMERG_TX_IDLE) {
        ESP_LOGW(TAG, "FALL_ALERT ignored in state %s", state_name(s_state));
        return;
    }

    s_abort = false;
    set_state(EMERG_TX_ALERT);
    ESP_LOGW(TAG, "FALL_ALERT — starting TX emergency workflow");

    if (run_stage(wisense_emergency_tx_stage_servo_open, "servo open") != ESP_OK) {
        shutdown_sequence();
        return;
    }
    if (s_abort) {
        shutdown_sequence();
        return;
    }

    if (wait_ms_abortable(CONFIG_WISENSE_EMERGENCY_TX_PRE_STREAM_DELAY_MS)) {
        ESP_LOGI(TAG, "startup aborted during pre-stream delay");
        shutdown_sequence();
        return;
    }

    if (run_stage(wisense_emergency_tx_stage_camera_start, "camera start") != ESP_OK) {
        shutdown_sequence();
        return;
    }
    if (s_abort) {
        shutdown_sequence();
        return;
    }

    if (run_stage(wisense_emergency_tx_stage_mic_start, "mic start") != ESP_OK) {
        shutdown_sequence();
        return;
    }
    if (s_abort) {
        shutdown_sequence();
        return;
    }

    if (run_stage(wisense_emergency_tx_stage_stream_start, "stream start") != ESP_OK) {
        shutdown_sequence();
        return;
    }
    if (s_abort) {
        shutdown_sequence();
        return;
    }

    set_state(EMERG_TX_RUNNING);
    ESP_LOGW(TAG, "TX emergency RUNNING (stream active)");
}

static void handle_fall_cancel(void)
{
    if (s_state == EMERG_TX_IDLE) {
        ESP_LOGI(TAG, "FALL_CANCEL ignored in state IDLE");
        return;
    }

    ESP_LOGI(TAG, "FALL_CANCEL — stopping TX emergency workflow");
    s_abort = true;
    shutdown_sequence();
}

static void worker_task(void *arg)
{
    (void)arg;

    emerg_tx_evt_t evt;

    for (;;) {
        if (xQueueReceive(s_evt_queue, &evt, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        switch (evt) {
        case EVT_FALL_ALERT:
            handle_fall_alert();
            break;
        case EVT_FALL_CANCEL:
            handle_fall_cancel();
            break;
        default:
            break;
        }
    }
}

static void post_event(emerg_tx_evt_t evt)
{
    if (s_evt_queue == NULL) {
        return;
    }

    if (xQueueSend(s_evt_queue, &evt, 0) != pdTRUE) {
        ESP_LOGW(TAG, "event queue full — dropped evt=%d", (int)evt);
    }
}

esp_err_t wisense_emergency_tx_init(void)
{
    if (s_worker_task != NULL) {
        return ESP_OK;
    }

    s_evt_queue = xQueueCreate(4, sizeof(emerg_tx_evt_t));
    ESP_RETURN_ON_FALSE(s_evt_queue != NULL, ESP_ERR_NO_MEM, TAG, "queue");

    s_state = EMERG_TX_IDLE;
    s_abort = false;

    const BaseType_t ok = xTaskCreate(
        worker_task,
        "wisense_emerg_tx",
        CONFIG_WISENSE_EMERGENCY_TX_TASK_STACK,
        NULL,
        CONFIG_WISENSE_EMERGENCY_TX_TASK_PRIO,
        &s_worker_task);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "task");

    ESP_LOGI(TAG, "TX emergency orchestrator ready (pre-stream delay=%d ms)",
             CONFIG_WISENSE_EMERGENCY_TX_PRE_STREAM_DELAY_MS);
    return ESP_OK;
}

void wisense_emergency_tx_on_alert(void)
{
    post_event(EVT_FALL_ALERT);
}

void wisense_emergency_tx_on_cancel(void)
{
    s_abort = true;
    post_event(EVT_FALL_CANCEL);
}

bool wisense_emergency_tx_is_active(void)
{
    return s_state != EMERG_TX_IDLE;
}
