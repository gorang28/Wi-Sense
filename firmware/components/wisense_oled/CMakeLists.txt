idf_component_register(SRCS "wisense_oled.c" "ssd1306_font.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_driver_i2c wisense_classifier)
