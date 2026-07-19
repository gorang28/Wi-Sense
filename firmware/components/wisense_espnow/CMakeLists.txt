idf_component_register(SRCS "wisense_espnow_proto.c"
                             "wisense_espnow_send.c"
                             "wisense_espnow_recv.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_wifi esp_event esp_netif)
