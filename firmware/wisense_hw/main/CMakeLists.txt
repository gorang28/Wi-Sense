idf_component_register(SRCS "app_main.c"
                       INCLUDE_DIRS "."
                       REQUIRES nvs_flash wisense_classifier wisense_oled wisense_light
                                  wisense_fsr wisense_emergency)
