#include "platform.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_timer.h>

void delay(uint32_t ms) { 
    vTaskDelay(pdMS_TO_TICKS(ms)); 
}

int64_t get_time(void) { 
    return esp_timer_get_time(); 
}

uint32_t millis(void) { 
    return (uint32_t)(esp_timer_get_time() / 1000); 
}

void flash(uint8_t count) {
    // Placeholder for LED flash or similar
}
