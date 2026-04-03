#!/bin/bash
# Flash ESP32-S3 XIAO Sense flight-streamer
# Usage: ./flash.sh [ap|sta|pair] [port] [port2]
#
# Modes:
#   sta   — Flash as station (default). Connects to SkyDrone AP.
#   ap    — Flash as access point. Creates SkyDrone network.
#   pair  — Flash two devices: first as AP, second as STA.
#
# Examples:
#   ./flash.sh                           # STA mode, auto-detect port
#   ./flash.sh ap                        # AP mode, auto-detect port
#   ./flash.sh sta /dev/cu.usbmodem1101  # STA mode, explicit port
#   ./flash.sh pair                      # Flash pair, auto-detect 2 ports
#   ./flash.sh pair /dev/cu.usbmodem31101 /dev/cu.usbmodem1101

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOARD_DIR="$SCRIPT_DIR"
PLATFORM_H="$BOARD_DIR/board_config/platform.h"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

MODE="${1:-sta}"

# --- Helpers ---
detect_ports() {
    case "$(uname -s)" in
        Darwin)  PORTS=($(ls /dev/cu.usbmodem* 2>/dev/null)) ;;
        Linux)   PORTS=($(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null)) ;;
        *)       echo -e "${RED}Error: Unsupported OS. Pass port manually.${NC}"; exit 1 ;;
    esac
}

set_wifi_mode() {
    local mode=$1  # 0=STA, 1=AP
    sed -i '' "s/#define ENABLE_WIFI_AP.*/#define ENABLE_WIFI_AP    $mode/" "$PLATFORM_H"
}

build_and_flash() {
    local port=$1
    cd "$BOARD_DIR"
    idf.py build
    idf.py -p "$port" flash
}

# --- Source ESP-IDF ---
source ~/skydev-research/esp/esp-idf/export.sh 2>/dev/null

# --- Execute mode ---
case "$MODE" in
    sta)
        if [ -n "$2" ]; then
            PORT="$2"
        else
            detect_ports
            if [ ${#PORTS[@]} -lt 1 ]; then
                echo -e "${RED}Error: No USB device found.${NC}"; exit 1
            fi
            PORT="${PORTS[0]}"
        fi
        echo -e "${GREEN}=== Flash STA → ${YELLOW}$PORT${NC} ${GREEN}===${NC}"
        set_wifi_mode 0
        build_and_flash "$PORT"
        echo -e "${GREEN}✓ STA flashed. Connects to SkyDrone AP.${NC}"
        ;;

    ap)
        if [ -n "$2" ]; then
            PORT="$2"
        else
            detect_ports
            if [ ${#PORTS[@]} -lt 1 ]; then
                echo -e "${RED}Error: No USB device found.${NC}"; exit 1
            fi
            PORT="${PORTS[0]}"
        fi
        echo -e "${GREEN}=== Flash AP → ${YELLOW}$PORT${NC} ${GREEN}===${NC}"
        set_wifi_mode 1
        build_and_flash "$PORT"
        echo -e "${GREEN}✓ AP flashed. SSID: SkyDrone, IP: 192.168.4.1${NC}"
        ;;

    pair)
        if [ -n "$2" ] && [ -n "$3" ]; then
            PORT_AP="$2"
            PORT_STA="$3"
        else
            detect_ports
            if [ ${#PORTS[@]} -lt 2 ]; then
                echo -e "${RED}Error: Need 2 USB devices, found ${#PORTS[@]}${NC}"
                echo "Detected: ${PORTS[*]}"
                exit 1
            fi
            PORT_AP="${PORTS[0]}"
            PORT_STA="${PORTS[1]}"
        fi
        echo -e "${GREEN}=== Flash Pair ===${NC}"
        echo -e "  AP:  ${YELLOW}$PORT_AP${NC}"
        echo -e "  STA: ${YELLOW}$PORT_STA${NC}"
        echo ""

        echo -e "${GREEN}[1/2] AP → $PORT_AP${NC}"
        set_wifi_mode 1
        build_and_flash "$PORT_AP"
        echo -e "${GREEN}  ✓ AP flashed${NC}"
        echo ""

        echo -e "${GREEN}[2/2] STA → $PORT_STA${NC}"
        set_wifi_mode 0
        build_and_flash "$PORT_STA"
        echo -e "${GREEN}  ✓ STA flashed${NC}"
        echo ""

        echo -e "${GREEN}=== Done ===${NC}"
        echo -e "  AP:  $PORT_AP  (SSID: SkyDrone, IP: 192.168.4.1)"
        echo -e "  STA: $PORT_STA (connects to SkyDrone)"
        ;;

    *)
        echo "Usage: ./flash.sh [ap|sta|pair] [port] [port2]"
        exit 1
        ;;
esac
