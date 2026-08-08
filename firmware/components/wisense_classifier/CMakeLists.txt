# Exactly one backend defines wisense_classifier_get(); the Kconfig choice
# picks which. Callers depend only on the ops table, so neither the peripheral
# modules nor app_main change when the backend does.
if(CONFIG_WISENSE_CLASSIFIER_TINYML)
    set(backend_src "classifier_tinyml.c")
else()
    set(backend_src "classifier_placeholder.c")
endif()

# REQUIRES has to cover both backends: component requirements are expanded in
# an early CMake pass where CONFIG_* values do not exist yet, so making this
# list conditional silently resolves to the wrong branch. SRCS above is fine —
# that is read in the normal pass, after Kconfig.
idf_component_register(SRCS "wisense_class.c" ${backend_src}
                       INCLUDE_DIRS "include"
                       REQUIRES freertos esp_driver_uart esp_driver_usb_serial_jtag
                                wisense_csi_infer)
