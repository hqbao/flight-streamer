idf_component_register(SRCS "main.c" "server.c" "platform.c"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES board_config esp_http_server esp_wifi nvs_flash driver esp_timer)
