#include "platform.h"
#include <led_strip.h>
#include <esp_timer.h>

// SuperMini ESP32-S3: WS2812 addressable RGB LED on GPIO 48
// LED states:
//   Not connected:  solid RED
//   Connecting:     white (R+G+B 33% each)
//   Connected idle: OFF
//   Sending data:   GREEN flash
//   Receiving data: BLUE flash

#define LED_BRIGHTNESS    80   // WS2812 per-channel (0-255)
#define LED_DIM           27   // ~33% of LED_BRIGHTNESS for connecting white
#define FLASH_DURATION_US (50 * 1000)   // 50ms data flash

static led_strip_handle_t g_led_strip;
static esp_timer_handle_t g_flash_timer;
static bool g_connected;

static void flash_off_callback(void *arg) {
    led_strip_clear(g_led_strip);
    led_strip_refresh(g_led_strip);
}

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

    esp_timer_create_args_t flash_args = {
        .callback = flash_off_callback,
        .name = "led_flash",
    };
    esp_timer_create(&flash_args, &g_flash_timer);
}

void led_not_connected(void) {
    g_connected = false;
    esp_timer_stop(g_flash_timer);
    led_strip_set_pixel(g_led_strip, 0, LED_BRIGHTNESS, 0, 0);  // solid RED
    led_strip_refresh(g_led_strip);
}

void led_connecting(void) {
    g_connected = false;
    esp_timer_stop(g_flash_timer);
    led_strip_set_pixel(g_led_strip, 0, LED_DIM, LED_DIM, LED_DIM);  // white
    led_strip_refresh(g_led_strip);
}

void led_connected(void) {
    g_connected = true;
    esp_timer_stop(g_flash_timer);
    led_strip_clear(g_led_strip);  // OFF
    led_strip_refresh(g_led_strip);
}

void led_send(void) {
    if (!g_connected) return;
    esp_timer_stop(g_flash_timer);
    led_strip_set_pixel(g_led_strip, 0, 0, LED_BRIGHTNESS, 0);  // GREEN
    led_strip_refresh(g_led_strip);
    esp_timer_start_once(g_flash_timer, FLASH_DURATION_US);
}

void led_recv(void) {
    if (!g_connected) return;
    esp_timer_stop(g_flash_timer);
    led_strip_set_pixel(g_led_strip, 0, 0, 0, LED_BRIGHTNESS);  // BLUE
    led_strip_refresh(g_led_strip);
    esp_timer_start_once(g_flash_timer, FLASH_DURATION_US);
}

void led_off(void) {
    esp_timer_stop(g_flash_timer);
    led_strip_clear(g_led_strip);
    led_strip_refresh(g_led_strip);
}
