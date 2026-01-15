#ifndef WIFI_CONNECT_H
#define WIFI_CONNECT_H

#include "esp_err.h"
#include "sdkconfig.h" // <--- Aquí viven las variables de Kconfig

#define WIFI_SSID      CONFIG_WIFI_SSID
#define WIFI_PASS      CONFIG_WIFI_PASSWORD
#define MAXIMUM_RETRY  CONFIG_WIFI_RETRY_MAX

/**
 * @brief Inicializa el WiFi en modo Estación (STA)
 */
esp_err_t wifi_connect_init(void);

#endif