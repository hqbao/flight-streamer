#include "platform.h"
#include <driver/gpio.h>
#include <esp_timer.h>

// XIAO ESP32-S3: simple active-low GPIO LED (no RGB — single color only)

#define FLASH_DURATION_US (50 * 1000)   // 50ms data flash

static esp_timer_handle_t g_flash_timer;
static bool g_connected;

static void flash_off_callback(void *arg) {
    gpio_set_level(LED_PIN, 1);  // off
}

void led_init(void) {
    gpio_reset_pin(LED_PIN);
    gpio_set_direction(LED_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(LED_PIN, 1);  // off (active-low)

    esp_timer_create_args_t flash_args = {
        .callback = flash_off_callback,
        .name = "led_flash",
    };
    esp_timer_create(&flash_args, &g_flash_timer);
}

void led_not_connected(void) {
    g_connected = false;
    esp_timer_stop(g_flash_timer);
    gpio_set_level(LED_PIN, 0);  // on
}

void led_connecting(void) {
    g_connected = false;
    esp_timer_stop(g_flash_timer);
    gpio_set_level(LED_PIN, 0);  // on
}

void led_connected(void) {
    g_connected = true;
    esp_timer_stop(g_flash_timer);
    gpio_set_level(LED_PIN, 1);  // off
}

void led_send(void) {
    if (!g_connected) return;
    esp_timer_stop(g_flash_timer);
    gpio_set_level(LED_PIN, 0);  // on
    esp_timer_start_once(g_flash_timer, FLASH_DURATION_US);
}

void led_recv(void) {
    if (!g_connected) return;
    esp_timer_stop(g_flash_timer);
    gpio_set_level(LED_PIN, 0);  // on
    esp_timer_start_once(g_flash_timer, FLASH_DURATION_US);
}

void led_off(void) {
    esp_timer_stop(g_flash_timer);
    gpio_set_level(LED_PIN, 1);  // off
}
