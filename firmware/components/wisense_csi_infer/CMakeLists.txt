# The exported trees are ~181 KB of .rodata, so nothing here is compiled unless
# the TinyML classifier backend is actually selected.
if(CONFIG_WISENSE_CLASSIFIER_TINYML)
    set(infer_srcs
        "wisense_gbdt.c"
        "wisense_csi_model.c"
        "wisense_csi_features.c"
        "wisense_csi_selftest.c"
        "wisense_csi_infer.c")
else()
    set(infer_srcs "")
endif()

idf_component_register(SRCS ${infer_srcs}
                    INCLUDE_DIRS "include"
                    PRIV_REQUIRES freertos esp_timer)
