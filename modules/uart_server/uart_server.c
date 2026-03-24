#include "uart_server.h"
#include <string.h>
#include <driver/uart.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include "pubsub.h"
#include "messages.h"
#include "platform.h"

#define TAG "uart_server"

#define FC_UART_PORT  UART_NUM_1
#define FC_BAUD_RATE  9600

#define DB_HEADER_SIZE 6
#define DB_FOOTER_SIZE 2

static TaskHandle_t g_rx_task_handle = NULL;

// ---------------------------------------------------------------------------
// UDP/USB → UART: forward received packets to flight controller
// ---------------------------------------------------------------------------
static void on_packet_to_uart(uint8_t *data, size_t size) {
    if (size < sizeof(db_packet_t)) return;
    db_packet_t *pkt = (db_packet_t *)data;
    if (!pkt->data || pkt->len == 0) return;

    led_data();
    uart_write_bytes(FC_UART_PORT, (const char *)pkt->data, pkt->len);
    led_connected();
}

// ---------------------------------------------------------------------------
// UART RX: parse complete DB packets, publish UART_RECEIVED
// ---------------------------------------------------------------------------
static void uart_rx_task(void *arg) {
    uint8_t rx_buf[128];
    uint8_t pkt_buf[256];
    int pkt_idx = 0;
    uint16_t payload_size = 0;
    int stage = 0;

    while (1) {
        int len = uart_read_bytes(FC_UART_PORT, rx_buf, sizeof(rx_buf),
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
                        publish(UART_RECEIVED, (uint8_t *)&pkt, sizeof(db_packet_t));
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
void uart_server_setup(void) {

    const uart_config_t cfg = {
        .baud_rate  = FC_BAUD_RATE,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };
    ESP_ERROR_CHECK(uart_param_config(FC_UART_PORT, &cfg));
    ESP_ERROR_CHECK(uart_driver_install(FC_UART_PORT, 1024, 0, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_set_pin(FC_UART_PORT, UART_TX_PIN, UART_RX_PIN,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    subscribe(UDP_RECEIVED, on_packet_to_uart);

    xTaskCreate(uart_rx_task, "uart_rx", 4096, NULL, 10, &g_rx_task_handle);

    ESP_LOGI(TAG, "UART%d @ %d baud", FC_UART_PORT, FC_BAUD_RATE);
}
