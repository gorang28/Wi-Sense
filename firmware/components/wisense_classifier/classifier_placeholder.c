/*
 * Placeholder classifier: maps UART keypresses to prediction classes.
 *
 *   e / E -> Empty
 *   p / P -> Presence
 *   m / M -> Motion
 *   f / F -> Fall
 *
 * When TinyML is ready, replace this file (via Kconfig) without changing
 * OLED, relay, or other automation modules.
 */
#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "esp_log.h"
#include "sdkconfig.h"

#include "wisense_classifier.h"

static const char *TAG = "clf_placeholder";

static SemaphoreHandle_t s_mutex;
static wisense_class_t s_current = WISENSE_CLASS_EMPTY;
static wisense_classifier_on_change_cb_t s_on_change;
static void *s_on_change_ctx;
static bool s_started;

static esp_err_t placeholder_init(void);
static esp_err_t placeholder_start(void);
static wisense_class_t placeholder_get(void);
static esp_err_t placeholder_set(wisense_class_t cls);
static esp_err_t placeholder_set_on_change(wisense_classifier_on_change_cb_t cb, void *ctx);

static const wisense_classifier_ops_t s_ops = {
    .init = placeholder_init,
    .start = placeholder_start,
    .get = placeholder_get,
    .set = placeholder_set,
    .set_on_change = placeholder_set_on_change,
};

const wisense_classifier_ops_t *wisense_classifier_get(void)
{
    return &s_ops;
}

static bool map_key_to_class(int c, wisense_class_t *out)
{
    switch (tolower(c)) {
    case 'e':
        *out = WISENSE_CLASS_EMPTY;
        return true;
    case 'p':
        *out = WISENSE_CLASS_PRESENCE;
        return true;
    case 'm':
        *out = WISENSE_CLASS_MOTION;
        return true;
    case 'f':
        *out = WISENSE_CLASS_FALL;
        return true;
    default:
        return false;
    }
}

static esp_err_t placeholder_set(wisense_class_t cls)
{
    if (cls < WISENSE_CLASS_EMPTY || cls > WISENSE_CLASS_FALL) {
        return ESP_ERR_INVALID_ARG;
    }

    if (s_mutex == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    wisense_class_t previous;
    wisense_classifier_on_change_cb_t cb;
    void *ctx;

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    previous = s_current;
    if (previous == cls) {
        xSemaphoreGive(s_mutex);
        return ESP_OK;
    }
    s_current = cls;
    cb = s_on_change;
    ctx = s_on_change_ctx;
    xSemaphoreGive(s_mutex);

    ESP_LOGI(TAG, "Class changed: %s -> %s",
             wisense_class_to_string(previous),
             wisense_class_to_string(cls));

    if (cb != NULL) {
        cb(cls, ctx);
    }

    return ESP_OK;
}

static wisense_class_t placeholder_get(void)
{
    wisense_class_t cls = WISENSE_CLASS_EMPTY;

    if (s_mutex == NULL) {
        return cls;
    }

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    cls = s_current;
    xSemaphoreGive(s_mutex);
    return cls;
}

static esp_err_t placeholder_set_on_change(wisense_classifier_on_change_cb_t cb, void *ctx)
{
    if (s_mutex == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    xSemaphoreTake(s_mutex, portMAX_DELAY);
    s_on_change = cb;
    s_on_change_ctx = ctx;
    xSemaphoreGive(s_mutex);
    return ESP_OK;
}

static void uart_reader_task(void *arg)
{
    (void)arg;

    ESP_LOGI(TAG, "UART reader ready — type e/p/m/f (Empty/Presence/Motion/Fall)");

    while (true) {
        uint8_t byte = 0;
        int n = uart_read_bytes(CONFIG_WISENSE_CLASSIFIER_UART_NUM, &byte, 1, pdMS_TO_TICKS(200));
        if (n <= 0) {
            continue;
        }

        /* Ignore line endings from terminal echo / Enter. */
        if (byte == '\r' || byte == '\n') {
            continue;
        }

        wisense_class_t cls;
        if (!map_key_to_class((int)byte, &cls)) {
            ESP_LOGD(TAG, "Ignored key: 0x%02x", byte);
            continue;
        }

        (void)placeholder_set(cls);
    }
}

static esp_err_t placeholder_init(void)
{
    if (s_mutex != NULL) {
        return ESP_OK;
    }

    s_mutex = xSemaphoreCreateMutex();
    if (s_mutex == NULL) {
        return ESP_ERR_NO_MEM;
    }

    s_current = WISENSE_CLASS_EMPTY;
    s_on_change = NULL;
    s_on_change_ctx = NULL;
    s_started = false;

    /*
     * Install the UART driver on the console port so we can read keys with
     * uart_read_bytes. Wire VFS stdin to the same driver for ESP_LOG / printf.
     */
    const int uart_num = CONFIG_WISENSE_CLASSIFIER_UART_NUM;
    esp_err_t err = uart_driver_install(uart_num, 256, 0, 0, NULL, 0);
    if (err == ESP_ERR_INVALID_STATE) {
        /* Already installed by console — continue. */
        err = ESP_OK;
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "uart_driver_install failed: %s", esp_err_to_name(err));
        return err;
    }

    uart_config_t cfg = {
        .baud_rate = CONFIG_WISENSE_CLASSIFIER_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    err = uart_param_config(uart_num, &cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "uart_param_config failed: %s", esp_err_to_name(err));
        return err;
    }

#if defined(CONFIG_ESP_CONSOLE_UART_DEFAULT) || defined(CONFIG_ESP_CONSOLE_UART_CUSTOM)
    uart_vfs_dev_use_driver(uart_num);
#endif

    ESP_LOGI(TAG, "Placeholder classifier init (UART%d @ %d baud, default=Empty)",
             uart_num, CONFIG_WISENSE_CLASSIFIER_UART_BAUD);
    return ESP_OK;
}

static esp_err_t placeholder_start(void)
{
    if (s_mutex == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (s_started) {
        return ESP_OK;
    }

    BaseType_t ok = xTaskCreate(uart_reader_task, "clf_uart", 3072, NULL, 5, NULL);
    if (ok != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    s_started = true;
    return ESP_OK;
}
