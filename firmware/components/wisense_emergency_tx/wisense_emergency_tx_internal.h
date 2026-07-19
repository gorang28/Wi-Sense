/*
 * Stage hooks for the TX emergency orchestrator.
 *
 * Weak stubs live in wisense_emergency_tx_stages.c. Later TX modules
 * (servo, camera, mic, stream) provide strong implementations.
 */
#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t wisense_emergency_tx_stage_servo_open(void);
esp_err_t wisense_emergency_tx_stage_servo_close(void);

esp_err_t wisense_emergency_tx_stage_camera_start(void);
esp_err_t wisense_emergency_tx_stage_camera_stop(void);

esp_err_t wisense_emergency_tx_stage_mic_start(void);
esp_err_t wisense_emergency_tx_stage_mic_stop(void);

esp_err_t wisense_emergency_tx_stage_stream_start(void);
esp_err_t wisense_emergency_tx_stage_stream_stop(void);

#ifdef __cplusplus
}
#endif
