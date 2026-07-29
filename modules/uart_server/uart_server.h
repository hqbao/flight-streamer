/*
 * uart_server — the wire to the flight controller. Owns UART1 (GPIO 43 TX / 44 RX, 115200)
 * and does nothing but move bytes:
 *   task uart_rx (priority 10): read raw bytes -> publish UART_RECEIVED
 *   subscribes UDP_RECEIVED  : write those bytes straight out of the UART
 *
 * PROTOCOL-AGNOSTIC BY DESIGN. This bridge never parses, reframes or validates the stream —
 * the endpoints (the flight controller, and the host tools) own their own framing, so the same
 * firmware carries the 'db' link, MAVLink or u-blox UBX without knowing which. Do not add a
 * parser here: the moment this code understands a frame, it can also corrupt or drop one.
 */
#ifndef UART_SERVER_H
#define UART_SERVER_H

void uart_server_setup(void);

#endif
