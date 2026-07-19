/*
 * Default (stub) stage implementations for TX emergency orchestrator.
 *
 * TX-2..TX-5 replace these with strong symbols in their own components.
 */
#include "esp_log.h"

#include "wisense_emergency_tx_internal.h"

static const char *TAG = "emerg_tx_stage";

#define STUB(name)                          \
    ESP_LOGI(TAG, "stub: " name);           \
    return ESP_OK

esp_err_t __attribute__((weak)) wisense_emergency_tx_stage_servo_open(void)
{
    STUB("servo open (privacy flap)");
}

esp_err_t __attribute__((weak)) wisense_emergency_tx_stage_servo_close(void)
{
    STUB("servo close (privacy flap)");
}

esp_err_t __attribute__((weak)) wisense_emergency_tx_stage_camera_start(void)
{
    STUB("camera start");
}

esp_err_t __attribute__((weak)) wisense_emergency_tx_stage_camera_stop(void)
{
    STUB("camera stop");
}

esp_err_t __attribute__((weak)) wisense_emergency_tx_stage_mic_start(void)
{
    STUB("mic start");
}

esp_err_t __attribute__((weak)) wisense_emergency_tx_stage_mic_stop(void)
{
    STUB("mic stop");
}

esp_err_t __attribute__((weak)) wisense_emergency_tx_stage_stream_start(void)
{
    STUB("stream start");
}

esp_err_t __attribute__((weak)) wisense_emergency_tx_stage_stream_stop(void)
{
    STUB("stream stop");
}
