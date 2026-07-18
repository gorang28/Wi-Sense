idf_component_register(SRCS "wisense_emergency.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_driver_gpio esp_timer wisense_oled wisense_classifier)
