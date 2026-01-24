#ifndef MQTT_APP_H
#define MQTT_APP_H

#include "as7265x.h"

extern bool modo_entrenamiento_activo;

// Inicia el WiFi y MQTT
void mqtt_app_start(void);

// Función para enviar datos (JSON)
void mqtt_app_send_full_spectrum(as7265x_values_t *vals);

#endif