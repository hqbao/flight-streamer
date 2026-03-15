cmake_minimum_required(VERSION 3.16)

# Calculate relative path to project root (flight-streamer)
get_filename_component(PROJECT_PATH "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

set(EXTRA_COMPONENT_DIRS
    "${PROJECT_PATH}/base/foundation"
    "board_config"
    "${PROJECT_PATH}/modules/wifi"
    "${PROJECT_PATH}/modules/udp_server"
    "${PROJECT_PATH}/modules/uart_server"
    "${PROJECT_PATH}/modules/usb_server"
    "main"
)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(flight-streamer)
