/*
 * TinyML classifier backend.
 *
 * Implements the same wisense_classifier_ops_t as the keyboard placeholder, so
 * light automation, the OLED and the fall-emergency state machine are unchanged
 * — they still only see a class and a change callback.
 *
 * All of the work (windowing, feature extraction, the three gradient-boosted
 * stages, smoothing and debounce) lives in the wisense_csi_infer component.
 * This file is only the adapter between that component's class enum and the
 * one the rest of the firmware uses.
 */
#include "esp_log.h"

#include "wisense_classifier.h"
#include "wisense_csi_infer.h"

_Static_assert((int)WISENSE_CSI_CLASS_EMPTY == (int)WISENSE_CLASS_EMPTY
                   && (int)WISENSE_CSI_CLASS_PRESENCE == (int)WISENSE_CLASS_PRESENCE
                   && (int)WISENSE_CSI_CLASS_MOTION == (int)WISENSE_CLASS_MOTION
                   && (int)WISENSE_CSI_CLASS_FALL == (int)WISENSE_CLASS_FALL,
               "wisense_csi_class_t must stay aligned with wisense_class_t");

static const char *TAG = "clf_tinyml";

static wisense_classifier_on_change_cb_t s_on_change;
static void *s_on_change_ctx;

static void on_inference_class(wisense_csi_class_t new_class,
                               const wisense_csi_prediction_t *detail, void *ctx)
{
    (void)ctx;

    ESP_LOGI(TAG, "Class changed: %s (occ=%.2f mot=%.2f fall=%.2f)",
             wisense_class_to_string((wisense_class_t)new_class),
             detail->p_occupied, detail->p_motion, detail->p_fall);

    if (s_on_change != NULL) {
        s_on_change((wisense_class_t)new_class, s_on_change_ctx);
    }
}

static esp_err_t tinyml_init(void)
{
    /* See wisense_csi_infer_init(): the project default log level is ERROR. */
    esp_log_level_set(TAG, ESP_LOG_INFO);

    esp_err_t err = wisense_csi_infer_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "inference init failed: %s", esp_err_to_name(err));
        return err;
    }
    return wisense_csi_infer_set_on_class(on_inference_class, NULL);
}

static esp_err_t tinyml_start(void)
{
    return wisense_csi_infer_start();
}

static wisense_class_t tinyml_get(void)
{
    return (wisense_class_t)wisense_csi_infer_get_class();
}

static esp_err_t tinyml_set(wisense_class_t cls)
{
    if (cls < WISENSE_CLASS_EMPTY || cls > WISENSE_CLASS_FALL) {
        return ESP_ERR_INVALID_ARG;
    }
    return wisense_csi_infer_set_class((wisense_csi_class_t)cls);
}

static esp_err_t tinyml_set_on_change(wisense_classifier_on_change_cb_t cb, void *ctx)
{
    s_on_change = cb;
    s_on_change_ctx = ctx;
    return ESP_OK;
}

static const wisense_classifier_ops_t s_ops = {
    .init = tinyml_init,
    .start = tinyml_start,
    .get = tinyml_get,
    .set = tinyml_set,
    .set_on_change = tinyml_set_on_change,
};

const wisense_classifier_ops_t *wisense_classifier_get(void)
{
    return &s_ops;
}
