idf_component_register(SRCS "app_main.c" "emergency_tx.c"
                    INCLUDE_DIRS "."
                    REQUIRES nvs_flash wisense_espnow wisense_emergency_tx)
