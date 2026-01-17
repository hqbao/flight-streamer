# Flight Streamer

A lightweight MJPEG video streaming server for ESP32-S3, designed for FPV (First Person View) and computer vision applications.

## Overview

Flight Streamer captures video frames from a camera module (e.g., OV2640, OV5640) and streams them over Wi-Fi using the HTTP Multipart protocol (MJPEG). This allows for low-latency video monitoring on web browsers or integration with CV pipelines (OpenCV, Python).

## Features

- **MJPEG Streaming:** Low-latency video via standard HTTP(S).
- **Wi-Fi Connectivity:** SoftAP or Station mode support.
- **Camera Support:** ESP32-Camera driver integration.
- **Simple Client:** View stream directly in any web browser.

## Getting Started

### Prerequisites

- ESP-IDF v5.x installed and sourced.

### Build and Flash

1. **Setup Environment**
   ```bash
   get_idf # or . $HOME/esp/esp-idf/export.sh
   ```

2. **Build the project**
   ```bash
   idf.py build
   ```

3. **Flash through USB/UART**
   ```bash
   idf.py -p <port> flash
   ```
   *Note: Replace `<port>` with your serial device (e.g., `/dev/ttyUSB0` or `/dev/cu.usbmodem...`)*

4. **Monitor Output**
   ```bash
   idf.py -p <port> monitor
   ```


- To clean the build
  idf.py clean

- To configure the project
  idf.py set-target esp32s3
  idf.py menuconfig

Tests:

1. Connect to wifi "dbcam", password is empty
2. Get video stream: http://192.168.4.1:81/stream
