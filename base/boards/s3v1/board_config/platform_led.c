#include "platform.h"
#include <driver/gpio.h>

// XIAO ESP32-S3: simple active-low GPIO LED

void led_init(void) {
    gpio_reset_pin(LED_PIN);
    gpio_set_direction(LED_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(LED_PIN, 1);  // off (active-low)
}

void led_connecting(void) {
    gpio_set_level(LED_PIN, 0);  // on
}

void led_connected(void) {
    gpio_set_level(LED_PIN, 1);  // off
}

void led_data(void) {
    gpio_set_level(LED_PIN, 0);  // on
}

void led_off(void) {
    gpio_set_level(LED_PIN, 1);  // off
}
