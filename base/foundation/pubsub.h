#ifndef PUBSUB_H
#define PUBSUB_H

#include <stddef.h>
#include <stdint.h>

typedef enum {
	// === Events ===
	WIFI_CONNECTED,
	UDP_RECEIVED,
	UART_RECEIVED,
	USB_RECEIVED,

	TOPIC_NULL
} topic_t;

typedef void (*subscriber_callback_t)(uint8_t *data, size_t size);

void publish(topic_t topic, uint8_t *data, size_t size);
void subscribe(topic_t topic, subscriber_callback_t callback);

#endif
