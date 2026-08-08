/*
 * FSR406 analog pressure sensor driver (ADC).
 *
 * wisense_fsr_is_pressed() is smoothed (simple moving average) and uses a
 * two-threshold Schmitt trigger (latched s_pressed) rather than a single
 * threshold, so a reading sitting near the boundary doesn't chatter.
 */
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_log.h"
#include "sdkconfig.h"

#include "wisense_fsr.h"

static const char *TAG = "wisense_fsr";

/* Simple IIR moving average: smoothed += (raw - smoothed) / N. */
#define FSR_SMOOTHING_N  4

static adc_oneshot_unit_handle_t s_adc;
static adc_channel_t s_channel;
static int s_gpio = -1;
static int s_last_raw;
static int s_smoothed;
static bool s_pressed;
static bool s_ready;

int wisense_fsr_read_raw(void)
{
    if (!s_ready) {
        return 0;
    }

    int raw = 0;
    if (adc_oneshot_read(s_adc, s_channel, &raw) != ESP_OK) {
        return s_last_raw;
    }

    s_last_raw = raw;
    s_smoothed += (raw - s_smoothed) / FSR_SMOOTHING_N;
    return raw;
}

esp_err_t wisense_fsr_init(int gpio)
{
    if (s_ready) {
        return ESP_OK;
    }

    if (gpio < 0) {
        gpio = CONFIG_WISENSE_FSR_GPIO;
    }

    adc_unit_t unit;
    ESP_RETURN_ON_ERROR(adc_oneshot_io_to_channel(gpio, &unit, &s_channel),
                        TAG, "gpio %d not ADC-capable", gpio);

    adc_oneshot_unit_init_cfg_t unit_cfg = {
        .unit_id = unit,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&unit_cfg, &s_adc), TAG, "adc unit");

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(s_adc, s_channel, &chan_cfg),
                        TAG, "adc channel");

    s_gpio = gpio;
    s_ready = true;

    s_last_raw = wisense_fsr_read_raw();
    /* Seed the average from the first sample so it doesn't ramp up from 0. */
    s_smoothed = s_last_raw;
    s_pressed = (s_smoothed >= CONFIG_WISENSE_FSR_PRESS_THRESHOLD);

    ESP_LOGI(TAG, "FSR ready (GPIO%d raw=%d press=%d release=%d)",
             s_gpio, s_last_raw, CONFIG_WISENSE_FSR_PRESS_THRESHOLD,
             CONFIG_WISENSE_FSR_RELEASE_THRESHOLD);
    return ESP_OK;
}

bool wisense_fsr_is_pressed(void)
{
    if (!s_ready) {
        return false;
    }

    (void)wisense_fsr_read_raw();

    if (s_pressed) {
        if (s_smoothed <= CONFIG_WISENSE_FSR_RELEASE_THRESHOLD) {
            s_pressed = false;
        }
    } else {
        if (s_smoothed >= CONFIG_WISENSE_FSR_PRESS_THRESHOLD) {
            s_pressed = true;
        }
    }

    return s_pressed;
}
