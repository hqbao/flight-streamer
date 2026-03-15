#!/bin/bash
# Flash two ESP32 devices: one as AP, one as STA
# Usage: ./flash_pair.sh [port1 port2]
#
# Automatically detects two USB ports, flashes the first as AP and second as STA.
# To swap which device is AP/STA, pass ports explicitly:
#   ./flash_pair.sh /dev/cu.usbmodem31101 /dev/cu.usbmodem1101

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOARD_DIR="$SCRIPT_DIR"
PLATFORM_H="$BOARD_DIR/board_config/platform.h"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# --- Find ports ---
if [ $# -ge 2 ]; then
    PORT_AP="$1"
    PORT_STA="$2"
else
    # Detect OS and find USB serial ports
    case "$(uname -s)" in
        Darwin)  PORTS=($(ls /dev/cu.usbmodem* 2>/dev/null)) ;;
        Linux)   PORTS=($(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null)) ;;
        *)       echo -e "${RED}Error: Unsupported OS. Pass ports manually:${NC}"
                 echo "  ./flash_pair.sh /dev/ttyACM0 /dev/ttyACM1"
                 exit 1 ;;
    esac
    if [ ${#PORTS[@]} -lt 2 ]; then
        echo -e "${RED}Error: Need 2 USB devices, found ${#PORTS[@]}${NC}"
        echo "Connect both ESP32 boards and try again."
        echo "Detected: ${PORTS[*]}"
        exit 1
    fi
    PORT_AP="${PORTS[0]}"
    PORT_STA="${PORTS[1]}"
fi

echo -e "${GREEN}=== Flash Pair ===${NC}"
echo -e "  AP  (Device A): ${YELLOW}$PORT_AP${NC}"
echo -e "  STA (Device B): ${YELLOW}$PORT_STA${NC}"
echo ""

# --- Source ESP-IDF ---
source ~/skydev-research/esp/esp-idf/export.sh 2>/dev/null

# --- Helper: set config flags ---
set_wifi_mode() {
    local mode=$1  # 0 or 1
    sed -i '' "s/#define ENABLE_WIFI_AP.*/#define ENABLE_WIFI_AP    $mode/" "$PLATFORM_H"
}

# --- Flash AP ---
echo -e "${GREEN}[1/2] Building & flashing AP (ENABLE_WIFI_AP=1) → $PORT_AP${NC}"
set_wifi_mode 1
cd "$BOARD_DIR"
idf.py build
idf.py -p "$PORT_AP" flash
echo -e "${GREEN}  ✓ AP flashed${NC}"
echo ""

# --- Flash STA ---
echo -e "${GREEN}[2/2] Building & flashing STA (ENABLE_WIFI_AP=0) → $PORT_STA${NC}"
set_wifi_mode 0
idf.py build
idf.py -p "$PORT_STA" flash
echo -e "${GREEN}  ✓ STA flashed${NC}"
echo ""

echo -e "${GREEN}=== Done ===${NC}"
echo -e "  AP:  $PORT_AP  (SSID: SkyDrone, IP: 192.168.4.1)"
echo -e "  STA: $PORT_STA (connects to SkyDrone)"
echo ""
echo "Test with: python3 $BOARD_DIR/../../../tools/test_uart_bridge.py"
