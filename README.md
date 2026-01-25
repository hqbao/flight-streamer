# Flight Streamer

A lightweight MJPEG video streaming server for ESP32-S3, designed for FPV (First Person View) and computer vision applications.

## Overview

Flight Streamer captures video frames from a camera module (e.g., OV2640) and streams them over Wi-Fi using the HTTP Multipart protocol (MJPEG). This allows for low-latency video monitoring on web browsers or integration with CV pipelines (OpenCV, Python).

The project is structured to support multiple boards, with the primary target being the ESP32-S3 (e.g., Seeed Studio XIAO ESP32S3 Sense).

## Features

- **MJPEG Streaming:** Low-latency video via standard HTTP at `http://<ip>:81/stream`.
- **Wi-Fi Connectivity:** Configured as a Wi-Fi Station (Client) to connect to an existing network.
- **Camera Support:** OV2640 (configurable for others).
- **Telemetry Bridge:** Contains UART bridge logic (Core 1) for flight controller telemetry (e.g., MSP or Mavlink).

## Project Structure

```
flight-streamer/
├── base/
│   └── boards/
│       └── s3v1/           # ESP32-S3 Board Implementation
│           ├── main/       # Application Source Code
│           ├── board_config/ # Hardware Pin Definitions
│           └── CMakeLists.txt
├── tools/
│   └── test_stream.py      # Python client for viewing the stream
└── README.md
```

## Getting Started

### Prerequisites

- **ESP-IDF v5.x**: Ensure the environment is set up and sourced.
- **Python 3**: For the viewer script.

### Configuration

Open `base/boards/s3v1/main/main.c` and configure your Wi-Fi credentials:

```c
#define WIFI_SSID         "YourSSID"
#define WIFI_PASS         "YourPassword"
```

### Build and Flash

1. **Setup Environment**
   ```bash
   . $HOME/esp/esp-idf/export.sh
   ```

2. **Navigate to Board Directory**
   ```bash
   cd base/boards/s3v1
   ```

3. **Build the project**
   ```bash
   idf.py build
   ```

4. **Flash to Device**
   Connect your ESP32-S3 via USB and find the port (e.g., `/dev/cu.usbmodem...`).
   ```bash
   idf.py -p <port> flash
   ```

5. **Monitor Output**
   ```bash
   idf.py -p <port> monitor
   ```
   *Note down the IP address printed in the logs (e.g., `got ip:192.168.1.54`).*

## Usage

### Viewing the Stream

Use the provided Python script to view the low-latency stream. 

```bash
# From the project root
python3 tools/test_stream.py <ip_address>
```

Replace `<ip_address>` with the IP obtained from the monitor output.
- **Quit**: Press `q` or close the window.
- **Reconnection**: The script automatically attempts to reconnect if the stream is lost.

## Troubleshooting

- **Connection Failed**: Check Wi-Fi credentials in `main.c` and ensure the ESP32 is within range.
- **No Video**: Ensure the camera ribbon cable is seated correctly.
- **Build Errors**: Try cleaning the build with `idf.py fullclean` and rebuilding.
