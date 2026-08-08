# WiSense hardware bring-up firmware (Module 1: OLED + placeholder classifier).
# Target: ESP32 DevKit for peripheral development; merge into csi_recv (ESP32-S3) later.
cmake_minimum_required(VERSION 3.16)

# Shared components live one level up so csi_recv can reuse them later.
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../components")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wisense_hw)
