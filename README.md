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
- **DB Protocol Parsing** — validates packet framing and checksums
- **Dual Serial** — USB-CDC + UART1 always active simultaneously
- **LED Status Indicator** — shows connection state and data activity (RGB colors on s3v2, on/off on s3v1)
- **Low Latency** — WiFi power save disabled, non-blocking UDP sends with `MSG_DONTWAIT`

## Project Structure

```
flight-streamer/
├── base/
│   ├── foundation/             # Platform abstraction, PubSub
│   │   ├── pubsub.h/c         #   Publish/Subscribe event system
│   │   └── messages.h          #   Shared message structs (db_packet_t)
│   └── boards/
│       ├── s3v1/               # XIAO ESP32-S3 Sense (8MB flash, PSRAM)
│       │   ├── board_config/   #   Hardware config + LED status driver (GPIO)
│       │   │   ├── platform.h  #   WiFi credentials, UART pins, LED pin
│       │   │   └── platform_led.c  # Active-low GPIO status LED
│       │   └── main/
│       │       └── main.c      #   LED init + module initialization
│       └── s3v2/               # SuperMini ESP32-S3 (4MB flash, no PSRAM)
│           ├── board_config/   #   Hardware config + LED status driver (WS2812)
│           │   ├── platform.h  #   WiFi credentials, UART pins, LED pin
│           │   └── platform_led.c  # WS2812 RGB status LED via RMT
│           └── main/
│               └── main.c      #   LED init + module initialization
│
├── modules/
│   ├── wifi/                   # WiFi AP or STA mode
│   ├── udp_server/             # UDP socket — sends/receives via PubSub
│   ├── uart_server/            # UART1 — DB packet parser for flight controller
│   └── usb_server/             # USB-CDC — DB packet parser for USB host
│
└── tools/
    └── test_uart_bridge.py     # GUI tool to test two-device wireless data link
```

## Architecture

### Data Flow

#### Single Device (wireless telemetry)

```
Python Tools ←── USB-CDC ──→ ESP32 ←── UART ──→ Flight Controller
                             └──── UDP/WiFi ────→ Peer ESP32
```

#### Two Devices (peer-to-peer relay)

```
┌────────┐  UART   ┌────────────┐  WiFi/UDP   ┌────────────┐  UART   ┌────────┐
│ Device │◄──────►│  ESP32-A   │◄───────────►│  ESP32-B   │◄──────►│ Device │
│  (FC)  │  9600  │   (AP)     │  port 8554  │   (STA)    │  9600  │  (FC)  │
└────────┘        └────────────┘             └────────────┘        └────────┘
```

STA auto-registers with AP at `192.168.4.1` on WiFi connect. AP registers
STA on first received packet and replies. Bidirectional UDP link established
automatically — no external bridge needed.

### PubSub Topics

| Topic | Publisher | Subscriber | Purpose |
|-------|-----------|------------|---------|
| `WIFI_CONNECTED` | wifi | udp_server | Start UDP socket after WiFi is ready |
| `UDP_RECEIVED` | udp_server | uart_server, usb_server | Forward UDP packets → UART + USB |
| `UART_RECEIVED` | uart_server | udp_server | Forward UART packets → UDP (to peer) |
| `USB_RECEIVED` | usb_server | udp_server | Forward USB packets → UDP (to peer) |

### Module Details

| Module | Task | Priority | Purpose |
|--------|------|----------|---------|
| `udp_server` | `udp_rx` | 5 | Receive UDP, publish `UDP_RECEIVED` |
| `uart_server` | `uart_rx` | 10 | Parse DB packets from UART1, publish `UART_RECEIVED` |
| `usb_server` | `usb_rx` | 10 | Parse DB packets from USB-CDC, publish `USB_RECEIVED` |
| `wifi` | — | — | WiFi init (AP or STA), publish `WIFI_CONNECTED` |

### DB Protocol

```
['d']['b'][ID][SubID][len_lo][len_hi][payload...][ck_lo][ck_hi]
```

- **Header**: 6 bytes (`'d'` `'b'` + ID + SubID + 16-bit LE length)
- **Checksum**: 16-bit sum (little-endian) over bytes 2 through end of payload
- UART parser validates length bounds to prevent buffer overflow

## Board Targets

| Target | Board | Flash | PSRAM | LED | Notes |
|--------|-------|-------|-------|-----|-------|
| `s3v1` | XIAO ESP32-S3 Sense | 8MB (2MB default) | 8MB Octal | GPIO 21 (active-low) | Original target |
| `s3v2` | SuperMini ESP32-S3 | 4MB | None | GPIO 48 (WS2812 RGB) | Compact, no camera |

Both boards share UART pins (GPIO 43 TX, GPIO 44 RX) and USB-Serial/JTAG.

### LED Status Indicator

The LED provides visual feedback for connection state and data activity:

**STA mode (connecting to AP):**

| State | s3v2 (WS2812 RGB) | s3v1 (GPIO) |
|-------|-------------------|-------------|
| Not connected | Solid RED | On |
| Connecting (retrying) | White (R+G+B) | On |
| Connected (idle) | OFF | Off |
| Sending data (UART TX) | GREEN flash | Flash |
| Receiving data (UART/USB RX) | BLUE flash | Flash |

**AP mode (hosting network):**

| State | s3v2 (WS2812 RGB) | s3v1 (GPIO) |
|-------|-------------------|-------------|
| No stations connected | Solid RED | On |
| Station connected (idle) | OFF | Off |
| Sending data (UART TX) | GREEN flash | Flash |
| Receiving data (UART/USB RX) | BLUE flash | Flash |

Data flashes are 50ms pulses using `esp_timer`. The LED API (`led_not_connected`, `led_connecting`, `led_connected`, `led_send`, `led_recv`, `led_off`) is board-specific — each board's `platform_led.c` maps these states to its hardware.

## Configuration

Edit `base/boards/<target>/board_config/platform.h`:

```c
// WiFi mode: 0 = STA (join router), 1 = AP (create hotspot)
#define ENABLE_WIFI_AP    0

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

**Notes:**
- WiFi power saving is disabled (`WIFI_PS_NONE`) for low-latency communication
- All ESP log output is suppressed since USB-CDC shares the data stream
- LED status driver is board-specific: `platform_led.c` in each board's `board_config/`

## Build & Flash

```bash
# Setup ESP-IDF environment
source ~/skydev-research/esp/esp-idf/export.sh

# Build (pick your board target)
cd flight-streamer/base/boards/s3v2   # or s3v1
idf.py build

# Flash to a specific port
idf.py -p /dev/cu.usbmodem1101 flash

# Flash and monitor
idf.py -p /dev/cu.usbmodem1101 flash monitor
```

### Using flash.sh

Each board has a `flash.sh` script that handles WiFi mode and port detection:

```bash
cd flight-streamer/base/boards/s3v2   # or s3v1

./flash.sh              # STA mode (default), auto-detect port
./flash.sh ap           # AP mode, auto-detect port
./flash.sh sta /dev/cu.usbmodem1101   # STA, explicit port
./flash.sh pair         # Flash two devices: first as AP, second as STA
./flash.sh pair /dev/cu.usbmodem31101 /dev/cu.usbmodem1101
```

## Testing

### Two-Device Bridge Test

Tests end-to-end data transmission between two ESP32 modules over WiFi.
The laptop communicates with each device via USB-CDC — the ESP32s
handle the WiFi link between themselves automatically.

1. Flash both devices:
   ```bash
   cd flight-streamer/base/boards/s3v2   # or s3v1
   ./flash.sh pair
   ```
2. Connect both devices to laptop via USB
3. Run the test tool:

```bash
python3 flight-streamer/tools/test_uart_bridge.py
```

Data flow: `Tool → USB-A → ESP32-A → WiFi → ESP32-B → USB-B → Tool`

Both USB-CDC and UART1 are always active — USB for host tools,
UART1 for flight controller connection. No mode switching needed.

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

## License

Proprietary. See LICENSE file for details.
