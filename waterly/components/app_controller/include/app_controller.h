#ifndef APP_CONTROLLER_H
#define APP_CONTROLLER_H

#include <stdbool.h>
#include "esp_err.h"

// DEFINICIÓN DE EVENTOS (INPUTS) ---
typedef enum {
    APP_EVENT_START_TRAINING, // Orden de medir continuo
    APP_EVENT_STOP_AND_SLEEP, // Orden de dormir
    APP_EVENT_SINGLE_MEASURE,    // Medir una vez
    APP_EVENT_START_OTA,
    APP_EVENT_GO_IDLE
} app_event_t;


// Inicia el controlador (crea la cola, lanza la tarea/hilo)
esp_err_t app_controller_init(void);

// Función para que otros componentes (MQTT) envíen eventos al cerebro
// Retorna true si se pudo encolar
bool app_controller_send_event(app_event_t event);

#endif