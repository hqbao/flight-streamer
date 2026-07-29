/*
 * wifi — brings up the radio as either an access point or a station, chosen at compile time by
 * ENABLE_WIFI_AP in the board's platform.h, and publishes WIFI_CONNECTED when the link is
 * usable (udp_server waits on that before opening its socket).
 *
 * Power saving is disabled (WIFI_PS_NONE) on purpose: the modem sleep this would otherwise use
 * adds tens of milliseconds of latency to telemetry that a pilot is reading live. In access
 * point mode the inactive-station timeout is 10 s so the status LED returns to "no peer"
 * promptly when the other end is powered off or walks out of range.
 */
#ifndef WIFI_H
#define WIFI_H

void wifi_setup(void);

#endif
