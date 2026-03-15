idf_component_register(SRCS "main.c"
                       INCLUDE_DIRS "."
                       REQUIRES wifi udp_server uart_server usb_server
                              foundation board_config nvs_flash esp_timer)
