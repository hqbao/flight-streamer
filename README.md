# DBLink — how to use

A wireless telemetry bridge on the ESP32-S3, replacing a SiK / RFD900 radio with WiFi. It
carries the flight controller's UART bytes over UDP to a peer board or to a laptop's USB, and
back. It never parses the stream, so it works for the `db` protocol, MAVLink or u-blox UBX
alike.

```
flight controller ──UART──► ESP32-A ──WiFi/UDP──► ESP32-B ──USB──► host tools
```

Both USB and UART1 are always live — there is no mode to switch.

---

## 1. Boards

| Target | Board | Flash | LED |
|---|---|---|---|
| `s3v1` | XIAO ESP32-S3 Sense | 8 MB (2 MB default), 8 MB octal PSRAM | GPIO 21, active-low |
| `s3v2` | SuperMini ESP32-S3 | 4 MB, no PSRAM | GPIO 48, WS2812 RGB |

Both use GPIO 43 TX / GPIO 44 RX at 115200 baud, and the built-in USB serial/JTAG.

## 2. Configure

Edit `base/boards/<target>/board_config/platform.h` before flashing:

```c
#define ENABLE_WIFI_AP    0            // 0 = join a router (station), 1 = be the hotspot
#define WIFI_STA_SSID     "YourSSID"
#define WIFI_STA_PASS     "YourPassword"
#define WIFI_AP_SSID      "SkyDrone"
#define WIFI_AP_PASS      "12345678"
#define UART_TX_PIN       43
#define UART_RX_PIN       44
```

## 3. Build and flash

```bash
source ~/skydev-research/esp/esp-idf/export.sh   # ESP-IDF environment

cd base/boards/s3v2            # or s3v1
./flash.sh                     # station mode, auto-detect the port
./flash.sh ap                  # access-point mode
./flash.sh sta /dev/cu.usbmodem1101      # explicit port
./flash.sh pair                # flash TWO boards: the first as access point, the second as station
```

Or drive ESP-IDF directly: `idf.py build`, `idf.py -p <port> flash monitor`.

Two boards pair themselves — no broker, no configuration. The station registers with the access
point at `192.168.4.1` when WiFi comes up; the access point learns the station from its first
packet.

## 4. Read the status LED

| State | RGB board (`s3v2`) | Single LED (`s3v1`) |
|---|---|---|
| Not connected / no peer | solid red | on |
| Connecting (retrying) | white | on |
| Connected, idle | off | off |
| Sending (UART transmit) | green flash | flash |
| Receiving (UART or USB) | blue flash | flash |

Flashes are 50 ms pulses. In access-point mode the LED returns to red about 10 s after the peer
disappears.

## 5. Test the link

```bash
pip install pyserial matplotlib
python3 tools/test_uart_bridge.py     # two boards on USB: send A->B and B->A, per-packet latency
python3 tools/bandwidth_lab.py        # throughput and loss: burst, paced, latency, rate sweep
```

## 6. Known limitation — the WiFi hop drops packets

Measured end to end (flight controller → station → WiFi → access point → host USB) with two
SuperMini boards side by side on one laptop: **about 69 % of frames are lost**, uniformly across
rates and frame sizes, and far below the 3840 B/s UART budget. Every frame that does arrive has
a valid checksum, and a UDP loopback on the access point showed no loss — so the loss is on the
radio hop between the two boards (weak onboard antennas, USB shielding). Before trusting a
telemetry number over this link, try in order:

1. Bring the boards within ~10 cm and re-measure — this alone often drops the loss below 5 %.
2. Pin a quiet 2.4 GHz channel and raise transmit power on both.
3. Use TCP instead of UDP on the board-to-board hop (lossless, 5–10 ms more latency).
4. Skip the radio: one board as a plain USB bridge.

## 7. Layout

| Path | What it is |
|---|---|
| `modules/uart_server/` | the flight-controller wire (UART1) |
| `modules/udp_server/` | the wireless half, peering and the UDP socket |
| `modules/usb_server/` | the host wire (USB serial/JTAG) |
| `modules/wifi/` | radio bring-up, access point or station |
| `base/boards/<target>/` | per-board pins, LED driver, configuration, `flash.sh` |
| `tools/` | the two host test tools |

Each module's header comment says what it publishes, what it subscribes to, and the constraint
that shaped it. `base/boards/*/managed_components/` is vendored ESP-IDF code — do not edit it.

## License

See [LICENSE](LICENSE).
