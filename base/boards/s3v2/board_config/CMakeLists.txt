idf_component_register(SRCS "platform_led.c"
                       INCLUDE_DIRS "."
                       REQUIRES led_strip esp_timer)
