#ifndef PLATFORM_H
#define PLATFORM_H

#include <inttypes.h>
#include <esp_timer.h>

void delay(uint32_t ms);
void flash(uint8_t count);
int64_t get_time(void);
uint32_t millis(void);

#endif
