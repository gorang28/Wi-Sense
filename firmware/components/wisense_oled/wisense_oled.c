/*
 * SSD1306 128x64 I2C driver for WiSense prediction display.
 *
 * Uses ESP-IDF 5.x i2c_master APIs only (no Arduino / u8g2).
 */
#include <stdio.h>
#include <string.h>

#include "driver/i2c_master.h"
#include "esp_check.h"
#include "esp_log.h"
#include "sdkconfig.h"

#include "ssd1306_font.h"
#include "wisense_oled.h"

static const char *TAG = "wisense_oled";

#define OLED_WIDTH   128
#define OLED_HEIGHT  64
#define OLED_PAGES   (OLED_HEIGHT / 8)

/** 2x upscale of the built-in 5x7 font for easier reading. */
#define FONT_SCALE   2
#define FONT_CHAR_W  (SSD1306_FONT_WIDTH * FONT_SCALE)
#define FONT_CHAR_H  (SSD1306_FONT_HEIGHT * FONT_SCALE)

/** Many 128x64 modules clip the leftmost columns; inset the frame slightly. */
#define BORDER_INSET_X  2
#define BORDER_INSET_Y  1

static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_dev;
static bool s_ready;
static uint8_t s_fb[OLED_WIDTH * OLED_PAGES];

static esp_err_t oled_write_cmd(uint8_t cmd)
{
    uint8_t buf[2] = {0x00, cmd}; /* Co=0, D/C#=0 → command */
    return i2c_master_transmit(s_dev, buf, sizeof(buf), 100);
}

static esp_err_t oled_write_cmd_arg(uint8_t cmd, uint8_t arg)
{
    ESP_RETURN_ON_ERROR(oled_write_cmd(cmd), TAG, "cmd 0x%02x", cmd);
    return oled_write_cmd(arg);
}

static esp_err_t oled_flush(void)
{
    /* Horizontal addressing: dump full framebuffer in one go per page. */
    for (int page = 0; page < OLED_PAGES; page++) {
        ESP_RETURN_ON_ERROR(oled_write_cmd((uint8_t)(0xB0 + page)), TAG, "page");
        ESP_RETURN_ON_ERROR(oled_write_cmd(0x00), TAG, "col_lo");
        ESP_RETURN_ON_ERROR(oled_write_cmd(0x10), TAG, "col_hi");

        uint8_t chunk[1 + OLED_WIDTH];
        chunk[0] = 0x40; /* Co=0, D/C#=1 → data */
        memcpy(&chunk[1], &s_fb[page * OLED_WIDTH], OLED_WIDTH);
        ESP_RETURN_ON_ERROR(i2c_master_transmit(s_dev, chunk, sizeof(chunk), 100),
                            TAG, "data page %d", page);
    }
    return ESP_OK;
}

static void fb_clear(void)
{
    memset(s_fb, 0, sizeof(s_fb));
}

static void fb_set_pixel(int x, int y, bool on)
{
    if (x < 0 || x >= OLED_WIDTH || y < 0 || y >= OLED_HEIGHT) {
        return;
    }
    int page = y / 8;
    int bit = y % 8;
    uint8_t *cell = &s_fb[page * OLED_WIDTH + x];
    if (on) {
        *cell |= (uint8_t)(1u << bit);
    } else {
        *cell &= (uint8_t)~(1u << bit);
    }
}

static void fb_draw_border(void)
{
    const int left = BORDER_INSET_X;
    const int right = OLED_WIDTH - 1 - BORDER_INSET_X;
    const int top = BORDER_INSET_Y;
    const int bottom = OLED_HEIGHT - 1 - BORDER_INSET_Y;

    for (int x = left; x <= right; x++) {
        fb_set_pixel(x, top, true);
        fb_set_pixel(x, bottom, true);
    }
    for (int y = top; y <= bottom; y++) {
        fb_set_pixel(left, y, true);
        fb_set_pixel(right, y, true);
    }
}

static void fb_draw_char(int x, int y, char c)
{
    const uint8_t *glyph = ssd1306_font_glyph(c);
    for (int col = 0; col < 5; col++) {
        uint8_t bits = glyph[col];
        for (int row = 0; row < 7; row++) {
            if (bits & (1u << row)) {
                for (int sy = 0; sy < FONT_SCALE; sy++) {
                    for (int sx = 0; sx < FONT_SCALE; sx++) {
                        fb_set_pixel(x + col * FONT_SCALE + sx,
                                     y + row * FONT_SCALE + sy,
                                     true);
                    }
                }
            }
        }
    }
}

static void fb_draw_string(int x, int y, const char *text)
{
    int cursor = x;
    for (const char *p = text; *p != '\0'; p++) {
        fb_draw_char(cursor, y, *p);
        cursor += FONT_CHAR_W;
        if (cursor >= OLED_WIDTH) {
            break;
        }
    }
}

static int string_pixel_width(const char *text)
{
    int n = (int)strlen(text);
    return n * FONT_CHAR_W;
}

static void fb_draw_string_centered(int y, const char *text)
{
    int text_w = string_pixel_width(text);
    int x = (OLED_WIDTH - text_w) / 2;
    if (x < 0) {
        x = 0;
    }
    fb_draw_string(x, y, text);
}

static esp_err_t ssd1306_panel_init(void)
{
    /* Standard 128x64 SSD1306 init sequence. */
    ESP_RETURN_ON_ERROR(oled_write_cmd(0xAE), TAG, "display off");
    ESP_RETURN_ON_ERROR(oled_write_cmd_arg(0xD5, 0x80), TAG, "clock");
    ESP_RETURN_ON_ERROR(oled_write_cmd_arg(0xA8, 0x3F), TAG, "mux");
    ESP_RETURN_ON_ERROR(oled_write_cmd_arg(0xD3, 0x00), TAG, "offset");
    ESP_RETURN_ON_ERROR(oled_write_cmd(0x40), TAG, "start line");
    ESP_RETURN_ON_ERROR(oled_write_cmd_arg(0x8D, 0x14), TAG, "charge pump");
    ESP_RETURN_ON_ERROR(oled_write_cmd_arg(0x20, 0x00), TAG, "horiz addr");
    ESP_RETURN_ON_ERROR(oled_write_cmd(0xA1), TAG, "seg remap");
    ESP_RETURN_ON_ERROR(oled_write_cmd(0xC8), TAG, "com scan dec");
    ESP_RETURN_ON_ERROR(oled_write_cmd_arg(0xDA, 0x12), TAG, "com pins");
    ESP_RETURN_ON_ERROR(oled_write_cmd_arg(0x81, 0xCF), TAG, "contrast");
    ESP_RETURN_ON_ERROR(oled_write_cmd_arg(0xD9, 0xF1), TAG, "precharge");
    ESP_RETURN_ON_ERROR(oled_write_cmd_arg(0xDB, 0x40), TAG, "vcom");
    ESP_RETURN_ON_ERROR(oled_write_cmd(0xA4), TAG, "resume ram");
    ESP_RETURN_ON_ERROR(oled_write_cmd(0xA6), TAG, "normal");
    ESP_RETURN_ON_ERROR(oled_write_cmd(0xAF), TAG, "display on");
    return ESP_OK;
}

esp_err_t wisense_oled_init(int i2c_sda_gpio, int i2c_scl_gpio)
{
    if (s_ready) {
        return ESP_OK;
    }

    if (i2c_sda_gpio < 0) {
        i2c_sda_gpio = CONFIG_WISENSE_OLED_I2C_SDA_GPIO;
    }
    if (i2c_scl_gpio < 0) {
        i2c_scl_gpio = CONFIG_WISENSE_OLED_I2C_SCL_GPIO;
    }

    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = CONFIG_WISENSE_OLED_I2C_PORT,
        .sda_io_num = i2c_sda_gpio,
        .scl_io_num = i2c_scl_gpio,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags = {
            .enable_internal_pullup = true,
        },
    };

    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &s_bus), TAG, "i2c bus");

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = CONFIG_WISENSE_OLED_I2C_ADDR,
        .scl_speed_hz = CONFIG_WISENSE_OLED_I2C_FREQ_HZ,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(s_bus, &dev_cfg, &s_dev),
                        TAG, "i2c device");

    esp_err_t err = ssd1306_panel_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SSD1306 init failed — check wiring/address (SDA=%d SCL=%d addr=0x%02X)",
                 i2c_sda_gpio, i2c_scl_gpio, CONFIG_WISENSE_OLED_I2C_ADDR);
        return err;
    }

    fb_clear();
    ESP_RETURN_ON_ERROR(oled_flush(), TAG, "initial flush");

    s_ready = true;
    ESP_LOGI(TAG, "OLED ready (SDA=%d SCL=%d addr=0x%02X)",
             i2c_sda_gpio, i2c_scl_gpio, CONFIG_WISENSE_OLED_I2C_ADDR);
    return ESP_OK;
}

esp_err_t wisense_oled_clear(void)
{
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    fb_clear();
    return oled_flush();
}

esp_err_t wisense_oled_show_class(wisense_class_t cls)
{
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    const char *label = wisense_class_to_string(cls);
    int text_w = string_pixel_width(label);
    int x = (OLED_WIDTH - text_w) / 2;
    if (x < 0) {
        x = 0;
    }
    int y = (OLED_HEIGHT - FONT_CHAR_H) / 2;

    fb_clear();
    fb_draw_border();
    fb_draw_string(x, y, label);
    ESP_RETURN_ON_ERROR(oled_flush(), TAG, "show_class flush");

    ESP_LOGI(TAG, "Display: %s", label);
    return ESP_OK;
}

esp_err_t wisense_oled_show_emergency(bool active, int countdown_sec)
{
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!active) {
        return ESP_OK;
    }

    char line2[16];
    const char *line3;

    fb_clear();
    fb_draw_border();

    if (countdown_sec > 0) {
        snprintf(line2, sizeof(line2), "%d sec", countdown_sec);
        line3 = "Press btn";
        fb_draw_string_centered(6, "EMERGENCY");
        fb_draw_string_centered(26, line2);
        fb_draw_string_centered(46, line3);
    } else {
        fb_draw_string_centered(6, "EMERGENCY");
        fb_draw_string_centered(26, "ALERT");
        fb_draw_string_centered(46, "SENT");
    }

    ESP_RETURN_ON_ERROR(oled_flush(), TAG, "show_emergency flush");
    ESP_LOGI(TAG, "Emergency display: countdown=%d", countdown_sec);
    return ESP_OK;
}
