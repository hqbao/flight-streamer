/*
 * usb_server — the wire to the host computer, over the ESP32's built-in USB serial/JTAG.
 *   task usb_rx (priority 10): read raw bytes -> publish USB_RECEIVED
 *   subscribes UDP_RECEIVED  : write those bytes to the host
 *
 * Every ESP log output is suppressed on this build precisely because this port carries binary
 * telemetry: one stray log line would land inside a frame and corrupt it.
 */
#ifndef USB_SERVER_H
#define USB_SERVER_H

void usb_server_setup(void);

#endif
