#include "usb_server.h"
#include <string.h>
#include <driver/usb_serial_jtag.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include "pubsub.h"
#include "messages.h"
#include "platform.h"

#define TAG "usb_server"

#define DB_HEADER_SIZE 6
#define DB_FOOTER_SIZE 2

static TaskHandle_t g_rx_task_handle = NULL;

// ---------------------------------------------------------------------------
// UDP → USB: forward received packets to USB host
// ---------------------------------------------------------------------------
static void on_packet_to_usb(uint8_t *data, size_t size) {
    if (size < sizeof(db_packet_t)) return;
    db_packet_t *pkt = (db_packet_t *)data;
    if (!pkt->data || pkt->len == 0) return;

    // Short timeout — if no USB host is connected, don't block the PubSub chain
    usb_serial_jtag_write_bytes((const char *)pkt->data, pkt->len,
                                20 / portTICK_PERIOD_MS);
}

// ---------------------------------------------------------------------------
// USB RX: parse complete DB packets, publish USB_RECEIVED
// ---------------------------------------------------------------------------
static void usb_rx_task(void *arg) {
    uint8_t rx_buf[128];
    uint8_t pkt_buf[256];
    int pkt_idx = 0;
    uint16_t payload_size = 0;
    int stage = 0;

    while (1) {
        int len = usb_serial_jtag_read_bytes(rx_buf, sizeof(rx_buf),
                                             20 / portTICK_PERIOD_MS);
        if (len <= 0) continue;

        for (int i = 0; i < len; i++) {
            uint8_t b = rx_buf[i];

            switch (stage) {
                case 0:
                    if (b == 'd') { pkt_buf[0] = b; pkt_idx = 1; stage = 1; }
                    break;
                case 1:
                    if (b == 'b') { pkt_buf[1] = b; pkt_idx = 2; stage = 2; }
                    else stage = 0;
                    break;
                case 2: case 3: case 4:
                    pkt_buf[pkt_idx++] = b;
                    stage++;
                    break;
                case 5:
                    pkt_buf[pkt_idx++] = b;
                    payload_size = pkt_buf[4] | (pkt_buf[5] << 8);
                    if (payload_size > sizeof(pkt_buf) - DB_HEADER_SIZE - DB_FOOTER_SIZE) {
                        stage = 0;
                    } else {
                        stage = 6;
                    }
                    break;
                case 6:
                    pkt_buf[pkt_idx++] = b;
                    if (pkt_idx >= DB_HEADER_SIZE + payload_size + DB_FOOTER_SIZE) {
                        led_data();
                        db_packet_t pkt = { .data = pkt_buf, .len = (size_t)pkt_idx };
                        publish(USB_RECEIVED, (uint8_t *)&pkt, sizeof(db_packet_t));
                        led_connected();
                        stage = 0;
                    }
                    break;
                default:
                    stage = 0;
                    break;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
void usb_server_setup(void) {
    usb_serial_jtag_driver_config_t usb_cfg = {
        .rx_buffer_size = 1024,
        .tx_buffer_size = 1024,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_cfg));

    subscribe(UDP_RECEIVED, on_packet_to_usb);

    xTaskCreate(usb_rx_task, "usb_rx", 4096, NULL, 10, &g_rx_task_handle);

    ESP_LOGI(TAG, "USB-CDC serial");
}
