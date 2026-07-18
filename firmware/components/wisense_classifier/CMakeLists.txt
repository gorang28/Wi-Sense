# Module 1 always builds the UART placeholder. TinyML backend will be
# added later via Kconfig without changing caller modules.
idf_component_register(SRCS "wisense_class.c" "classifier_placeholder.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_driver_uart freertos)
