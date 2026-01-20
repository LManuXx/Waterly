#ifndef MQTT_APP_H
#define MQTT_APP_H

extern bool modo_entrenamiento_activo;

// Inicia el WiFi y MQTT
void mqtt_app_start(void);

// Función para enviar datos (JSON)
void mqtt_app_send_data(int uv, int vis, int nir);

#endif