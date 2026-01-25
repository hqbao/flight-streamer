#include <stdio.h>
#include <string.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/event_groups.h>
#include <esp_camera.h>
#include <driver/uart.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#include <esp_mac.h>
#include <esp_log.h>
#include <nvs_flash.h>
#include <string.h>
#include "server.h"
#include "platform.h"

#define TAG "main.c"

#include "board.h"

#define RC_FREQ           20
#define WIFI_SSID         "Bao"
#define WIFI_PASS         "nopassword"
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

typedef struct {
  uint8_t byte;
  uint8_t buffer[128];
  uint8_t header[2];
  char stage;
  uint16_t payload_size;
  int buffer_idx;
} uart_rx_t;

TaskHandle_t task_handle_1 = NULL;
TaskHandle_t task_handle_2 = NULL;

static EventGroupHandle_t s_wifi_event_group;


volatile char g_frame_captured = 0;

static rc_t g_rc;
static int g_roll = 0;
static int g_pitch = 0;
static int g_yaw = 0;

static void fc_init(void) {
  const uart_config_t uart_config = {
    .baud_rate = 19200,
    .data_bits = UART_DATA_8_BITS,
    .parity = UART_PARITY_DISABLE,
    .stop_bits = UART_STOP_BITS_1,
    .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
    .source_clk = UART_SCLK_APB,
  };
  ESP_ERROR_CHECK(uart_param_config(UART_NUM_1, &uart_config));
  ESP_ERROR_CHECK(uart_driver_install(UART_NUM_1, 1024, 0, 0, NULL, 0));
  ESP_ERROR_CHECK(uart_param_config(UART_NUM_1, &uart_config));
  ESP_ERROR_CHECK(uart_set_pin(UART_NUM_1, 43, 44, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
}

static void rc_timer(void *param) {
  rc_get(&g_rc);
}

static void handle_db_msg(uart_rx_t *msg) {
  if (msg->header[0] == 'b' && msg->header[1] == 'd') { // DB message
    if (msg->buffer[0] == 0x00 && msg->buffer[1] == 0x00) { // Euler Angle
      g_roll = *(int*)&msg->buffer[4];
      g_pitch = *(int*)&msg->buffer[8];
      g_yaw = *(int*)&msg->buffer[12];
      // ESP_LOGI(TAG, "Roll: %d\tPitch: %d\tYaw: %d\t", g_roll/1000, g_pitch/1000, g_yaw/1000);
    }
  }
}

static void init_cam(void) {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_QVGA;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 10;
  config.fb_count = 2;

  ESP_ERROR_CHECK(esp_camera_init(&config));
}

static void event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
  static int s_retry_num = 0;
  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
    esp_wifi_connect();
  } 
  else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
    if (s_retry_num < 100) {
      esp_wifi_connect();
      s_retry_num++;
      ESP_LOGI(TAG, "Retry to connect to the AP");
    } 
    else {
      xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
    }
    ESP_LOGI(TAG,"connect to the AP fail");
  } 
  else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
    ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
    ESP_LOGI(TAG, "got ip:" IPSTR, IP2STR(&event->ip_info.ip));
    s_retry_num = 0;
    xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
  }
}

static void wifi_connect(void) {
  s_wifi_event_group = xEventGroupCreate();

  ESP_ERROR_CHECK(esp_netif_init());

  ESP_ERROR_CHECK(esp_event_loop_create_default());
  esp_netif_create_default_wifi_sta();

  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&cfg));

  esp_event_handler_instance_t instance_any_id;
  esp_event_handler_instance_t instance_got_ip;
  ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, &instance_any_id));
  ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, &instance_got_ip));

  wifi_config_t wifi_config = {
    .sta = {
      .ssid = WIFI_SSID,
      .password = WIFI_PASS,
    },
  };
  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA) );
  ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config) );
  ESP_ERROR_CHECK(esp_wifi_start() );

  ESP_LOGI(TAG, "wifi_init_sta finished.");

  /* Waiting until either the connection is established (WIFI_CONNECTED_BIT) or connection failed for the maximum
   * number of re-tries (WIFI_FAIL_BIT). The bits are set by event_handler() (see above) */
  EventBits_t bits = xEventGroupWaitBits(
    s_wifi_event_group,
    WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
    pdFALSE,
    pdFALSE,
    portMAX_DELAY);

  /* xEventGroupWaitBits() returns the bits before the call returned, hence we can test which event actually
   * happened. */
  if (bits & WIFI_CONNECTED_BIT) {
    ESP_LOGI(TAG, "connected to ap SSID:%s password:%s", WIFI_SSID, WIFI_PASS);
    server_start();
  } 
  else if (bits & WIFI_FAIL_BIT) {
    ESP_LOGI(TAG, "Failed to connect to SSID:%s, password:%s", WIFI_SSID, WIFI_PASS);
  }
  else {
    ESP_LOGE(TAG, "UNEXPECTED EVENT");
  }
}

void core0() {
  init_cam();

  // Initialize NVS
  esp_err_t ret = nvs_flash_init();
  if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    ret = nvs_flash_init();
  }
  ESP_ERROR_CHECK(ret);
  wifi_connect();

  while (1) {delay(1000);}
}

void core1() {
  // Init timer
  fc_init();

  // Start tranceivering RC values
  const esp_timer_create_args_t timer_args1 = {
    .callback = &rc_timer,
    .name = "RC timer"
  };
  esp_timer_handle_t timer_handler1;
  ESP_ERROR_CHECK(esp_timer_create(&timer_args1, &timer_handler1));
  ESP_ERROR_CHECK(esp_timer_start_periodic(timer_handler1, 1000000/RC_FREQ));

  uart_rx_t g_uart_rx1 = {0, {0}, {0}, 0, 0, 0};
  while (1) {
    int len = uart_read_bytes(UART_NUM_1, &g_uart_rx1.byte, 1, 20 / portTICK_PERIOD_MS);
    if (len != 1) continue;

    if (g_uart_rx1.stage == 5) {
      g_uart_rx1.buffer[g_uart_rx1.buffer_idx] = g_uart_rx1.byte;
      g_uart_rx1.buffer_idx++;
      // Plus 2-byte class-id, 2-byte length and 2-byte checksum
      if (g_uart_rx1.buffer_idx == g_uart_rx1.payload_size + 6) {
        g_uart_rx1.stage = 0;
        handle_db_msg(&g_uart_rx1);
      }
    }
    else if (g_uart_rx1.stage == 0) {
      if (g_uart_rx1.byte == 'b') {
        g_uart_rx1.header[0] = g_uart_rx1.byte;
        g_uart_rx1.stage = 1;
      }
    }
    else if (g_uart_rx1.stage == 1) {
      if (g_uart_rx1.byte == 'd') {
        g_uart_rx1.header[1] = g_uart_rx1.byte;
        g_uart_rx1.buffer_idx = 0;
        g_uart_rx1.stage = 2;
      }
      else g_uart_rx1.stage = 0;
    }
    else if (g_uart_rx1.stage == 2) {
      g_uart_rx1.buffer[g_uart_rx1.buffer_idx] = g_uart_rx1.byte;
      g_uart_rx1.buffer_idx = 1;
      g_uart_rx1.stage = 3;
    }
    else if (g_uart_rx1.stage == 3) {
      g_uart_rx1.buffer[g_uart_rx1.buffer_idx] = g_uart_rx1.byte;
      g_uart_rx1.buffer_idx = 2;
      g_uart_rx1.stage = 4;
    }
    else if (g_uart_rx1.stage == 4) {
      g_uart_rx1.buffer[g_uart_rx1.buffer_idx] = g_uart_rx1.byte;
      g_uart_rx1.buffer_idx++;
      if (g_uart_rx1.buffer_idx == 4) {
        g_uart_rx1.payload_size = *(uint16_t*)&g_uart_rx1.buffer[2];
        g_uart_rx1.stage = 5;
      }
    }
    else {
      g_uart_rx1.stage = 0;
    }
  }
}

void app_main(void) {
  ESP_LOGI(TAG, "Start program");

  xTaskCreatePinnedToCore(core0, "Core 0 loop", 4096, NULL, 2, &task_handle_1, 0);
  xTaskCreatePinnedToCore(core1, "Core 1 loop", 4096, NULL, 3, &task_handle_2, 1);

  while (1) {delay(1000);}
}
