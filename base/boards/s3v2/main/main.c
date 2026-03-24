#include <stdio.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <nvs_flash.h>
#include <esp_log.h>

#include "platform.h"
#include "pubsub.h"
#include "wifi.h"
#include "udp_server.h"
#include "uart_server.h"
#include "usb_server.h"

#define TAG "main"

void app_main(void) {
    // Suppress log output — USB-CDC shares the same port as our data stream
    esp_log_level_set("*", ESP_LOG_NONE);

    // Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // LED — show connecting status from boot
    led_init();
    led_connecting();

    // Initialize modules
    udp_server_setup();
    uart_server_setup();
    usb_server_setup();
    wifi_setup();
}
