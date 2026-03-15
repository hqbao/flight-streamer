#include "udp_server.h"
#include <string.h>
#include <errno.h>
#include <lwip/sockets.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include "pubsub.h"
#include "messages.h"
#include "platform.h"

#define TAG "udp_server"
#define UDP_PORT 8554

static int g_sock = -1;
static struct sockaddr_in g_peer_addr;
static volatile bool g_peer_registered = false;
static TaskHandle_t g_rx_task_handle = NULL;

// ---------------------------------------------------------------------------
// UART → UDP: forward telemetry packets to peer
// ---------------------------------------------------------------------------
static void on_uart_received(uint8_t *data, size_t size) {
    if (!g_peer_registered || g_sock < 0) return;
    if (size < sizeof(db_packet_t)) return;

    db_packet_t *pkt = (db_packet_t *)data;
    if (!pkt->data || pkt->len == 0) return;

    sendto(g_sock, pkt->data, pkt->len, MSG_DONTWAIT,
           (struct sockaddr *)&g_peer_addr,
           sizeof(g_peer_addr));
}

// ---------------------------------------------------------------------------
// UDP RX: receive from peer → publish UDP_RECEIVED
// ---------------------------------------------------------------------------
static void udp_rx_task(void *arg) {
    uint8_t buf[256];
    socklen_t addr_len = sizeof(g_peer_addr);

    ESP_LOGI(TAG, "Listening on port %d", UDP_PORT);

    while (1) {
        int len = recvfrom(g_sock, buf, sizeof(buf), 0,
                           (struct sockaddr *)&g_peer_addr, &addr_len);
        if (len <= 0) continue;

        if (!g_peer_registered) {
            ESP_LOGI(TAG, "Peer: %s:%d",
                     inet_ntoa(g_peer_addr.sin_addr),
                     ntohs(g_peer_addr.sin_port));
            // Reply so peer registers us too
            sendto(g_sock, "r", 1, 0,
                   (struct sockaddr *)&g_peer_addr, sizeof(g_peer_addr));
        }
        g_peer_registered = true;

        // Forward valid DB packets to UART
        if (len >= 8 && buf[0] == 'd' && buf[1] == 'b') {
            db_packet_t pkt = { .data = buf, .len = (size_t)len };
            publish(UDP_RECEIVED, (uint8_t *)&pkt, sizeof(db_packet_t));
        }
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
static void start_udp(void) {
    struct sockaddr_in addr = {
        .sin_family      = AF_INET,
        .sin_port        = htons(UDP_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };

    g_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (g_sock < 0) {
        ESP_LOGE(TAG, "Socket failed");
        return;
    }

    if (bind(g_sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        ESP_LOGE(TAG, "Bind failed");
        close(g_sock);
        g_sock = -1;
        return;
    }

    ESP_LOGI(TAG, "UDP on port %d", UDP_PORT);

    // STA mode: AP is always at 192.168.4.1 — register immediately
#if !ENABLE_WIFI_AP
    g_peer_addr.sin_family = AF_INET;
    g_peer_addr.sin_port   = htons(UDP_PORT);
    inet_aton("192.168.4.1", &g_peer_addr.sin_addr);
    g_peer_registered = true;
    sendto(g_sock, "r", 1, 0,
           (struct sockaddr *)&g_peer_addr, sizeof(g_peer_addr));
    ESP_LOGI(TAG, "Peer: 192.168.4.1:%d (AP)", UDP_PORT);
#endif

    xTaskCreate(udp_rx_task, "udp_rx", 4096, NULL, 5, &g_rx_task_handle);
}

static void on_wifi_connected(uint8_t *data, size_t size) {
    start_udp();
}

void udp_server_setup(void) {
    subscribe(WIFI_CONNECTED, on_wifi_connected);
    subscribe(UART_RECEIVED, on_uart_received);
    subscribe(USB_RECEIVED, on_uart_received);
}
