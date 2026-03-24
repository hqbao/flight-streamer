#include "platform.h"
#include <led_strip.h>

// SuperMini ESP32-S3: WS2812 addressable RGB LED on GPIO 48

static led_strip_handle_t g_led_strip;

void led_init(void) {
    led_strip_config_t strip_config = {
        .strip_gpio_num = LED_PIN,
        .max_leds = 1,
    };
    led_strip_rmt_config_t rmt_config = {
        .resolution_hz = 10 * 1000 * 1000,  // 10 MHz
    };
    led_strip_new_rmt_device(&strip_config, &rmt_config, &g_led_strip);
    led_strip_clear(g_led_strip);
}

void led_connecting(void) {
    led_strip_set_pixel(g_led_strip, 0, 0, 0, 12);  // blue
    led_strip_refresh(g_led_strip);
}

void led_connected(void) {
    led_strip_set_pixel(g_led_strip, 0, 0, 8, 0);   // dim green
    led_strip_refresh(g_led_strip);
}

void led_data(void) {
    led_strip_set_pixel(g_led_strip, 0, 0, 40, 0);  // bright green
    led_strip_refresh(g_led_strip);
}

void led_off(void) {
    led_strip_clear(g_led_strip);
}
