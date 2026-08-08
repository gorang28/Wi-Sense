/*
 * Privacy-flap servo — LEDC PWM, driven by angle in degrees.
 */
#include "driver/ledc.h"
#include "esp_check.h"
#include "esp_log.h"
#include "sdkconfig.h"

#include "wisense_servo.h"

static const char *TAG = "wisense_servo";

#define SERVO_LEDC_MODE     LEDC_LOW_SPEED_MODE
#define SERVO_LEDC_TIMER    LEDC_TIMER_0
#define SERVO_LEDC_CHANNEL  LEDC_CHANNEL_0
#define SERVO_LEDC_RES_BITS LEDC_TIMER_14_BIT

static bool s_ready;

static int angle_to_pulse_us(int angle_deg)
{
    const int min_us = CONFIG_WISENSE_SERVO_MIN_PULSE_US;
    const int max_us = CONFIG_WISENSE_SERVO_MAX_PULSE_US;
    return min_us + ((max_us - min_us) * angle_deg) / 180;
}

static esp_err_t servo_set_angle(int angle_deg)
{
    ESP_RETURN_ON_FALSE(s_ready, ESP_ERR_INVALID_STATE, TAG, "not init");

    const int pulse_us = angle_to_pulse_us(angle_deg);
    const int period_us = 1000000 / CONFIG_WISENSE_SERVO_PWM_FREQ_HZ;
    const uint32_t max_duty = (1u << SERVO_LEDC_RES_BITS) - 1;
    const uint32_t duty = (uint32_t)(((int64_t)pulse_us * max_duty) / period_us);

    ESP_RETURN_ON_ERROR(ledc_set_duty(SERVO_LEDC_MODE, SERVO_LEDC_CHANNEL, duty), TAG, "set duty");
    ESP_RETURN_ON_ERROR(ledc_update_duty(SERVO_LEDC_MODE, SERVO_LEDC_CHANNEL), TAG, "update duty");
    return ESP_OK;
}

esp_err_t wisense_servo_init(int gpio)
{
    if (s_ready) {
        return ESP_OK;
    }

    if (gpio < 0) {
        gpio = CONFIG_WISENSE_SERVO_GPIO;
    }

    ledc_timer_config_t timer_cfg = {
        .speed_mode = SERVO_LEDC_MODE,
        .timer_num = SERVO_LEDC_TIMER,
        .duty_resolution = SERVO_LEDC_RES_BITS,
        .freq_hz = CONFIG_WISENSE_SERVO_PWM_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer_cfg), TAG, "timer config");

    ledc_channel_config_t chan_cfg = {
        .gpio_num = gpio,
        .speed_mode = SERVO_LEDC_MODE,
        .channel = SERVO_LEDC_CHANNEL,
        .timer_sel = SERVO_LEDC_TIMER,
        .duty = 0,
        .hpoint = 0,
    };
    ESP_RETURN_ON_ERROR(ledc_channel_config(&chan_cfg), TAG, "channel config");

    s_ready = true;

    ESP_LOGI(TAG, "Servo ready (GPIO%d, %d Hz, closed=%d° open=%d°)",
             gpio, CONFIG_WISENSE_SERVO_PWM_FREQ_HZ,
             CONFIG_WISENSE_SERVO_CLOSED_ANGLE_DEG, CONFIG_WISENSE_SERVO_OPEN_ANGLE_DEG);

    /* Force closed at boot regardless of whatever position it powered up in. */
    return servo_set_angle(CONFIG_WISENSE_SERVO_CLOSED_ANGLE_DEG);
}

esp_err_t wisense_servo_open(void)
{
    esp_err_t err = servo_set_angle(CONFIG_WISENSE_SERVO_OPEN_ANGLE_DEG);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Flap open (%d°)", CONFIG_WISENSE_SERVO_OPEN_ANGLE_DEG);
    }
    return err;
}

esp_err_t wisense_servo_close(void)
{
    esp_err_t err = servo_set_angle(CONFIG_WISENSE_SERVO_CLOSED_ANGLE_DEG);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Flap closed (%d°)", CONFIG_WISENSE_SERVO_CLOSED_ANGLE_DEG);
    }
    return err;
}
