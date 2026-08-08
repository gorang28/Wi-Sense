idf_component_register(SRCS "wisense_light.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_driver_gpio esp_timer wisense_classifier wisense_fsr)
