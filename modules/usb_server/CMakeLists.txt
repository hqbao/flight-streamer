idf_component_register(SRCS "usb_server.c"
                       INCLUDE_DIRS "."
                       REQUIRES foundation board_config driver freertos)
