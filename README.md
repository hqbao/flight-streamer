# Flight Streamer

A wireless UART↔UDP bridge for ESP32-S3, replacing traditional RF telemetry radios (SiK/RFD900) with WiFi.

## Overview

Flight Streamer runs on an ESP32-S3 and bridges the flight controller's UART telemetry to UDP over WiFi. Python tools on a laptop communicate with the flight controller wirelessly — same DB protocol, just WiFi transport instead of a USB cable.

```
Python Tools ←── UDP/WiFi ──→ ESP32-S3 ←── UART ──→ Flight Controller (STM32H7)
```

The project uses a strict **Event-Driven PubSub Architecture** — all inter-module communication goes through publish/subscribe, never direct function calls.

## Features

- **Bidirectional UART↔UDP Bridge** — DB protocol packets forwarded transparently
- **WiFi AP Mode** — ESP32 creates its own hotspot (no router needed)
- **WiFi STA Mode** — ESP32 joins an existing network, retries indefinitely
- **Auto-Peer** — STA registers with AP on connect, AP registers STA on first packet
- **DB Protocol Parsing** — validates packet framing and checksums on UART RX
- **Low Latency** — non-blocking UDP sends with `MSG_DONTWAIT`

## Project Structure

```
flight-streamer/
├── base/
│   ├── foundation/             # Platform abstraction, PubSub
│   │   ├── pubsub.h/c         #   Publish/Subscribe event system
│   │   └── messages.h          #   Shared message structs (db_packet_t)
│   └── boards/
│       └── s3v1/               # ESP32-S3 board (Xiao ESP32-S3 Sense)
│           ├── board_config/   #   Hardware config
│           │   └── platform.h  #   WiFi credentials, UART pins, feature flags
│           └── main/
│               └── main.c      #   Module initialization
│
├── modules/
│   ├── wifi/                   # WiFi AP or STA mode
│   ├── udp_server/             # UDP socket — sends/receives via PubSub
│   └── uart_server/            # UART — DB packet parser, sends/receives via PubSub
│
└── tools/
    └── test_uart_bridge.py     # GUI tool to test two-device wireless data link
```

## Architecture

### Data Flow

#### Single Device (wireless telemetry)

```
Python Tools ←── UDP/WiFi ──→ ESP32 ←── UART ──→ Flight Controller
```

#### Two Devices (peer-to-peer relay)

```
┌────────┐  UART   ┌────────────┐  WiFi/UDP   ┌────────────┐  UART   ┌────────┐
│ Device │◄──────►│  ESP32-A   │◄───────────►│  ESP32-B   │◄──────►│ Device │
│  (FC)  │ 38400  │   (AP)     │  port 8554  │   (STA)    │ 38400  │  (FC)  │
└────────┘        └────────────┘             └────────────┘        └────────┘
```

STA auto-registers with AP at `192.168.4.1` on WiFi connect. AP registers
STA on first received packet and replies. Bidirectional UDP link established
automatically — no external bridge needed.

### PubSub Topics

| Topic | Publisher | Subscriber | Purpose |
|-------|-----------|------------|---------|
| `WIFI_CONNECTED` | wifi | udp_server | Start UDP socket after WiFi is ready |
| `UDP_RECEIVED` | udp_server | uart_server | Forward UDP packets → UART (to FC) |
| `UART_RECEIVED` | uart_server | udp_server | Forward UART packets → UDP (to client) |

### Module Details

| Module | Task | Priority | Purpose |
|--------|------|----------|---------|
| `udp_server` | `udp_rx` | 5 | Receive UDP, publish `UDP_RECEIVED` |
| `uart_server` | `uart_rx` | 10 | Parse DB packets from UART, publish `UART_RECEIVED` |
| `wifi` | — | — | WiFi init (AP or STA), publish `WIFI_CONNECTED` |

### DB Protocol

```
['d']['b'][ID][SubID][len_lo][len_hi][payload...][ck_a][ck_b]
```

- **Header**: 6 bytes (`'d'` `'b'` + ID + SubID + 16-bit LE length)
- **Checksum**: Fletcher-8 over bytes 2 through end of payload
- UART parser validates length bounds to prevent buffer overflow

## Configuration

Edit `base/boards/s3v1/board_config/platform.h`:

```c
// WiFi mode: 0 = STA (join router), 1 = AP (create hotspot)
#define ENABLE_WIFI_AP    0

// Serial I/O: 0 = UART1 on GPIO 43/44, 1 = USB-CDC (test via USB cable)
#define UART_USE_USB      0

// STA mode credentials
#define WIFI_STA_SSID     "YourSSID"
#define WIFI_STA_PASS     "YourPassword"

// AP mode credentials
#define WIFI_AP_SSID      "SkyDrone"
#define WIFI_AP_PASS      "12345678"

// UART to flight controller
#define UART_TX_PIN       43
#define UART_RX_PIN       44
```

## Build & Flash

```bash
# Setup ESP-IDF environment
source ~/skydev-research/esp/esp-idf/export.sh

# Build
cd flight-streamer/base/boards/s3v1
idf.py build

# Flash to a specific port
idf.py -p /dev/cu.usbmodem1101 flash

# Flash and monitor
idf.py -p /dev/cu.usbmodem1101 flash monitor

# List available ports
ls /dev/cu.usbmodem*
```

### Flashing Two Devices (AP + STA)

1. Set `ENABLE_WIFI_AP=1` in `platform.h`, build, flash to Device A:
   ```bash
   idf.py build && idf.py -p /dev/cu.usbmodem1101 flash
   ```
2. Set `ENABLE_WIFI_AP=0` and `WIFI_STA_SSID="SkyDrone"` in `platform.h`, build, flash to Device B:
   ```bash
   idf.py build && idf.py -p /dev/cu.usbmodem31101 flash
   ```
3. To identify which port is which device, unplug one and run `ls /dev/cu.usbmodem*`

## Testing

### Two-Device Bridge Test

Tests end-to-end data transmission between two ESP32 modules over WiFi.
The laptop communicates with each device via USB only — the ESP32s
handle the WiFi link between themselves automatically.

1. Flash both devices with USB-CDC enabled for testing:
   ```bash
   cd flight-streamer/base/boards/s3v1
   ./flash_pair.sh --usb
   ```
2. Connect both devices to laptop via USB
3. Run the test tool:

```bash
python3 flight-streamer/tools/test_uart_bridge.py
```

Data flow: `Tool → USB-A → ESP32-A → WiFi → ESP32-B → USB-B → Tool`

> **Note:** `--usb` sets `UART_USE_USB=1` so data goes through the USB cable.
> For production (connecting to flight controller), re-flash without `--usb`
> to use UART1 on GPIO 43/44.

The tool provides:
- Two UART port selectors (one per device)
- Send A→B / B→A test packets with sequence numbers
- Latency measurement per packet
- Auto-send mode with configurable interval
- Dual log panels with color-coded TX/RX messages

**Dependencies**: `pip install pyserial`

## Related Projects

| Project | Description |
|---------|-------------|
| [flight-controller](../flight-controller/) | STM32H7 autopilot (consumer of this bridge) |
| [flight-optflow](../flight-optflow/) | ESP32-S3 optical flow sensor module |
