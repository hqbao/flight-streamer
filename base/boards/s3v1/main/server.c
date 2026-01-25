#include "server.h"

#include <esp_http_server.h>
#include <esp_timer.h>
#include <esp_camera.h>
#include <esp_log.h>
#include "platform.h"

#define TAG "server.c"

#define PART_BOUNDARY "123456789000000000000987654321"
static const char *_STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *_STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char *_STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\nX-Timestamp: %d.%06d\r\n\r\n";

httpd_handle_t stream_httpd = NULL;
httpd_handle_t web_httpd = NULL;

char g_rc_buf[48] = {0,};

void rc_get(rc_t *rc) {
  rc->roll     = (int) atoi(&g_rc_buf[0]);
  rc->pitch    = (int) atoi(&g_rc_buf[8]);
  rc->yaw      = (int) atoi(&g_rc_buf[16]);
  rc->throttle = (int) atoi(&g_rc_buf[24]);
  rc->on       = (int) atoi(&g_rc_buf[32]);
  rc->mode     = (int) atoi(&g_rc_buf[40]);
}

static esp_err_t index_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, "It works", 9);
}

static esp_err_t cmd_handler(httpd_req_t *req) {
  memset(g_rc_buf, 0, 48);

  httpd_req_get_hdr_value_str(req, "rc1", &g_rc_buf[0], 8);
  httpd_req_get_hdr_value_str(req, "rc2", &g_rc_buf[8], 8);
  httpd_req_get_hdr_value_str(req, "rc3", &g_rc_buf[16], 8);
  httpd_req_get_hdr_value_str(req, "rc4", &g_rc_buf[24], 8);
  httpd_req_get_hdr_value_str(req, "rc5", &g_rc_buf[32], 8);
  httpd_req_get_hdr_value_str(req, "rc6", &g_rc_buf[40], 8);

  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, "OK", 3);
}

static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t *fb = NULL;
  struct timeval _timestamp;
  esp_err_t res = ESP_OK;
  size_t _jpg_buf_len = 0;
  uint8_t *_jpg_buf = NULL;
  char *part_buf[128];

  res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
  if (res != ESP_OK) {
    return res;
  }

  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "X-Framerate", "60");

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) {
      ESP_LOGI(TAG, "Camera capture failed");
      res = ESP_FAIL;
    }
    else {
      _timestamp.tv_sec = fb->timestamp.tv_sec;
      _timestamp.tv_usec = fb->timestamp.tv_usec;

      _jpg_buf_len = fb->len;
      _jpg_buf = fb->buf;

      if (res == ESP_OK) {
        res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
      }

      if (res == ESP_OK) {
        size_t hlen = snprintf((char *)part_buf, 128, _STREAM_PART, _jpg_buf_len, _timestamp.tv_sec, _timestamp.tv_usec);
        res = httpd_resp_send_chunk(req, (const char *)part_buf, hlen);
      }

      if (res == ESP_OK) {
        res = httpd_resp_send_chunk(req, (const char *)_jpg_buf, _jpg_buf_len);
      }

      if (fb) {
        esp_camera_fb_return(fb);
        fb = NULL;
        _jpg_buf = NULL;
      }
      else if (_jpg_buf) {
        free(_jpg_buf);
        _jpg_buf = NULL;
      }

      if (res != ESP_OK) {
        ESP_LOGI(TAG, "Send frame failed");
        break;
      }
    }
  }
    
  return res;
}

void server_start(void) {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();

  httpd_uri_t index_uri = {
    .uri = "/",
    .method = HTTP_GET,
    .handler = index_handler,
    .user_ctx = NULL
  };

  httpd_uri_t cmd_uri = {
    .uri = "/cmd",
    .method = HTTP_POST,
    .handler = cmd_handler,
    .user_ctx = NULL
  };

  httpd_uri_t stream_uri = {
    .uri = "/stream",
    .method = HTTP_GET,
    .handler = stream_handler,
    .user_ctx = NULL
  };

  ESP_LOGI(TAG, "Starting web server on port: %d", config.server_port);
  if (httpd_start(&web_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(web_httpd, &index_uri);
    httpd_register_uri_handler(web_httpd, &cmd_uri);
  }

  config.server_port += 1;
  config.ctrl_port += 1;
  ESP_LOGI(TAG, "Starting stream server on port: %d", config.server_port);
  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}
