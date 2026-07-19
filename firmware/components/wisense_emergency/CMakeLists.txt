idf_component_register(SRCS "wisense_emergency_rx.c"
                             "wisense_emergency_button.c"
                             "wisense_emergency_buzzer.c"
                             "wisense_emergency_indicator.c"
                             "wisense_emergency_notify.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_driver_gpio esp_timer wisense_oled wisense_classifier
                                  wisense_espnow)
