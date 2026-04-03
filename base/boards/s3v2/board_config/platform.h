#ifndef PLATFORM_H
#define PLATFORM_H

#include <stdio.h>
#include <inttypes.h>

// === WiFi Configuration ===
#define ENABLE_WIFI_AP    0

#define WIFI_STA_SSID     "SkyDrone"
#define WIFI_STA_PASS     "12345678"

#define WIFI_AP_SSID      "SkyDrone"
#define WIFI_AP_PASS      "12345678"   // min 8 chars, or "" for open
#define WIFI_AP_CHANNEL   1
#define WIFI_AP_MAX_CONN  2

// === Debug ===
#define ENABLE_DEBUG_LOGGING 0

#define platform_console(fmt, ...) printf(fmt, ##__VA_ARGS__)

// === Serial Interface ===
#define UART_TX_PIN       43
#define UART_RX_PIN       44

// === LED ===
// SuperMini ESP32-S3 has a WS2812 RGB LED on GPIO 48
#define LED_PIN           48

void led_init(void);
void led_not_connected(void);  // solid RED — no WiFi
void led_connecting(void);     // white (R+G+B) — STA connecting
void led_connected(void);      // OFF — WiFi ready
void led_send(void);           // GREEN flash — sending data
void led_recv(void);           // BLUE flash — receiving data
void led_off(void);

#endif
