/*
 * Relay + digital LDR light automation.
 */
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "wisense_fsr.h"
#include "wisense_light.h"

static const char *TAG = "wisense_light";

static int s_relay_gpio = -1;
static int s_ldr_gpio = -1;
static bool s_ready;
static bool s_relay_on;
static wisense_class_t s_prev_class = WISENSE_CLASS_EMPTY;
static wisense_class_t s_current_class = WISENSE_CLASS_EMPTY;
static esp_timer_handle_t s_empty_timer;
static esp_timer_handle_t s_ldr_poll_timer;

static int relay_on_level(void)
{
#if CONFIG_WISENSE_LIGHT_RELAY_ON_LOW
    return 0;
#else
    return 1;
#endif
}

static int relay_off_level(void)
{
    return relay_on_level() ? 0 : 1;
}

static esp_err_t relay_set(bool on)
{
    ESP_RETURN_ON_FALSE(s_ready, ESP_ERR_INVALID_STATE, TAG, "not init");

    int level = on ? relay_on_level() : relay_off_level();
    ESP_RETURN_ON_ERROR(gpio_set_level(s_relay_gpio, level), TAG, "relay gpio");

    s_relay_on = on;
    ESP_LOGI(TAG, "Relay %s (GPIO%d=%d)", on ? "ON" : "OFF", s_relay_gpio, level);
    return ESP_OK;
}

static bool class_is_occupied(wisense_class_t cls)
{
    return cls == WISENSE_CLASS_PRESENCE || cls == WISENSE_CLASS_MOTION;
}

static void empty_timer_stop(void)
{
    if (s_empty_timer != NULL) {
        esp_timer_stop(s_empty_timer);
    }
}

static void empty_timer_start(void)
{
    empty_timer_stop();
    esp_timer_start_once(s_empty_timer,
                         (uint64_t)CONFIG_WISENSE_LIGHT_EMPTY_OFF_SEC * 1000000ULL);
}

static void ldr_poll_stop(void)
{
    if (s_ldr_poll_timer != NULL) {
        esp_timer_stop(s_ldr_poll_timer);
    }
}

static void ldr_poll_start(void)
{
    ldr_poll_stop();
    esp_timer_start_periodic(s_ldr_poll_timer,
                             (uint64_t)CONFIG_WISENSE_LIGHT_LDR_POLL_MS * 1000ULL);
}

static void empty_timer_cb(void *arg)
{
    (void)arg;

    if (s_current_class != WISENSE_CLASS_EMPTY) {
        ESP_LOGI(TAG, "Empty timer fired but class is %s — relay unchanged",
                 wisense_class_to_string(s_current_class));
        return;
    }

    if (!s_relay_on) {
        ESP_LOGD(TAG, "Empty timer fired — relay already OFF");
        return;
    }

    ESP_LOGI(TAG, "Empty for %d s — relay OFF",
             CONFIG_WISENSE_LIGHT_EMPTY_OFF_SEC);
    (void)relay_set(false);
}

static void apply_occupied_light_policy(void)
{
    if (!class_is_occupied(s_current_class)) {
        return;
    }

    /* Presence + pressure on bed = sleeping → keep light off. */
    if (s_current_class == WISENSE_CLASS_PRESENCE && wisense_fsr_is_pressed()) {
        if (s_relay_on) {
            ESP_LOGI(TAG, "Sleeping on bed (Presence + pressure) — relay OFF");
            (void)relay_set(false);
        }
        return;
    }

    if (wisense_light_is_dark() && !s_relay_on) {
        ESP_LOGI(TAG, "Room dark while %s — relay ON",
                 wisense_class_to_string(s_current_class));
        (void)relay_set(true);
    }
}

static void ldr_poll_cb(void *arg)
{
    (void)arg;
    apply_occupied_light_policy();
}

static void handle_empty_to_occupied(wisense_class_t new_class)
{
    if (s_current_class == WISENSE_CLASS_PRESENCE && wisense_fsr_is_pressed()) {
        ESP_LOGI(TAG, "Empty -> Presence on bed — light stays OFF (sleeping)");
    } else if (wisense_light_is_dark()) {
        ESP_LOGI(TAG, "Empty -> %s in dark room — relay ON",
                 wisense_class_to_string(new_class));
    } else {
        ESP_LOGI(TAG, "Empty -> %s but room is bright — light stays OFF, watching LDR",
                 wisense_class_to_string(new_class));
    }
    apply_occupied_light_policy();
}

esp_err_t wisense_light_init(int relay_gpio, int ldr_gpio)
{
    if (s_ready) {
        return ESP_OK;
    }

    if (relay_gpio < 0) {
        relay_gpio = CONFIG_WISENSE_LIGHT_RELAY_GPIO;
    }
    if (ldr_gpio < 0) {
        ldr_gpio = CONFIG_WISENSE_LIGHT_LDR_GPIO;
    }

    gpio_config_t relay_cfg = {
        .pin_bit_mask = (1ULL << relay_gpio),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&relay_cfg), TAG, "relay gpio_config");

    gpio_config_t ldr_cfg = {
        .pin_bit_mask = (1ULL << ldr_gpio),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&ldr_cfg), TAG, "ldr gpio_config");

    const esp_timer_create_args_t empty_timer_args = {
        .callback = empty_timer_cb,
        .name = "empty_off",
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&empty_timer_args, &s_empty_timer), TAG, "empty timer");

    const esp_timer_create_args_t ldr_poll_args = {
        .callback = ldr_poll_cb,
        .name = "ldr_poll",
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&ldr_poll_args, &s_ldr_poll_timer), TAG, "ldr poll timer");

    s_relay_gpio = relay_gpio;
    s_ldr_gpio = ldr_gpio;
    s_prev_class = WISENSE_CLASS_EMPTY;
    s_current_class = WISENSE_CLASS_EMPTY;
    s_ready = true;

    ESP_RETURN_ON_ERROR(relay_set(false), TAG, "relay off");

    ESP_LOGI(TAG, "Light automation ready (relay=GPIO%d on=%s, LDR=GPIO%d dark=%s, empty_off=%ds, ldr_poll=%dms)",
             s_relay_gpio,
             relay_on_level() ? "HIGH" : "LOW",
             s_ldr_gpio,
#if CONFIG_WISENSE_LIGHT_LDR_DARK_IS_LOW
             "LOW",
#else
             "HIGH",
#endif
             CONFIG_WISENSE_LIGHT_EMPTY_OFF_SEC,
             CONFIG_WISENSE_LIGHT_LDR_POLL_MS);
    return ESP_OK;
}

esp_err_t wisense_light_relay_off(void)
{
    return relay_set(false);
}

esp_err_t wisense_light_relay_on(void)
{
    return relay_set(true);
}

bool wisense_light_is_dark(void)
{
    if (!s_ready) {
        return false;
    }

    int level = gpio_get_level(s_ldr_gpio);
#if CONFIG_WISENSE_LIGHT_LDR_DARK_IS_LOW
    return level == 0;
#else
    return level != 0;
#endif
}

esp_err_t wisense_light_on_class_change(wisense_class_t new_class)
{
    ESP_RETURN_ON_FALSE(s_ready, ESP_ERR_INVALID_STATE, TAG, "not init");

    wisense_class_t previous = s_prev_class;
    s_prev_class = new_class;
    s_current_class = new_class;

    empty_timer_stop();

    if (new_class == WISENSE_CLASS_EMPTY) {
        ldr_poll_stop();
        if (class_is_occupied(previous)) {
            ESP_LOGI(TAG, "%s -> Empty — relay OFF in %d s if still empty",
                     wisense_class_to_string(previous),
                     CONFIG_WISENSE_LIGHT_EMPTY_OFF_SEC);
            empty_timer_start();
        }
        return ESP_OK;
    }

    if (class_is_occupied(new_class)) {
        if (previous == WISENSE_CLASS_EMPTY) {
            handle_empty_to_occupied(new_class);
        } else {
            apply_occupied_light_policy();
        }
        ldr_poll_start();
        return ESP_OK;
    }

    ldr_poll_stop();
    return ESP_OK;
}
