/*
 * udp_server — the wireless half of the bridge (UDP port 8554).
 *   task udp_rx (priority 5): receive a datagram -> publish UDP_RECEIVED
 *   subscribes UART_RECEIVED and USB_RECEIVED : send those bytes to the peer
 *   subscribes WIFI_CONNECTED : opens the socket only once the network is actually up
 *
 * PEERING IS AUTOMATIC, with no external broker: the station registers itself with the access
 * point at 192.168.4.1 the moment WiFi connects, and the access point learns the station from
 * its first received packet and replies to that address. Two boards therefore form a
 * bidirectional link on their own — one flashed as access point, one as station.
 */
#ifndef UDP_SERVER_H
#define UDP_SERVER_H

void udp_server_setup(void);

#endif
