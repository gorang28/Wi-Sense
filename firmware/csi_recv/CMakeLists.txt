# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.16)

# Shared WiSense hardware peripheral components (OLED, relay/LDR, FSR,
# fall emergency, classifier). csi_recv is the real ESP32-S3 RX target.
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../components")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(csi_recv)
