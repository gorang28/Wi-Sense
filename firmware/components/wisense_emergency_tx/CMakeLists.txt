idf_component_register(SRCS "wisense_emergency_tx.c"
                             "wisense_emergency_tx_stages.c"
                       INCLUDE_DIRS "include"
                       REQUIRES freertos esp_timer)
