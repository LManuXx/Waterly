#ifndef CONFIG_MANAGER_H
#define CONFIG_MANAGER_H

#include <stdbool.h>
#include "esp_err.h"

#define CONFIG_NAMESPACE "waterly"
#define CONFIG_VERSION 1

// --- DEFAULTS ---
#define DEFAULT_WIFI_SSID      ""
#define DEFAULT_WIFI_PASS      ""
#define DEFAULT_WIFI_RETRY_MAX 5

#define DEFAULT_BLE_POP        "waterly123"

#define DEFAULT_MQTT_BROKER_IP "192.168.50.136"
#define DEFAULT_MQTT_HOSTNAME  "waterly"
#define DEFAULT_MQTT_CLIENT_ID "waterly_esp32"
#define DEFAULT_MQTT_TOPIC_CMD "waterly/comandos"
#define DEFAULT_MQTT_TOPIC_DAT "waterly/datos"
#define DEFAULT_MQTT_KEEPALIVE 120
#define DEFAULT_MQTT_RECONNECT_MS 5000

#define DEFAULT_SENSOR_GAIN         3  // 64x
#define DEFAULT_SENSOR_INTEGRATION  50 // 50 * 2.8ms = 140ms
#define DEFAULT_SENSOR_LED_CURRENT  0  // 12.5mA
#define DEFAULT_SENSOR_POLL_MS      10
#define DEFAULT_SENSOR_TIMEOUT_MS   1500

#define DEFAULT_NEXTION_BAUD 9600
#define DEFAULT_NEXTION_TX   17
#define DEFAULT_NEXTION_RX   16

#define DEFAULT_OTA_URL "https://raw.githubusercontent.com/LManuXx/Waterly/main/waterly/version.json"

#define DEFAULT_TRAINING_INTERVAL_MS 3000
#define DEFAULT_DEEP_SLEEP_MIN       1

// --- ESTRUCTURA DE CONFIGURACION ---
typedef struct {
    // WiFi
    char wifi_ssid[33];
    char wifi_pass[65];
    int  wifi_retry_max;

    // BLE
    char ble_pop[33];

    // MQTT
    char mqtt_broker_ip[16];
    char mqtt_hostname[33];
    char mqtt_client_id[33];
    char mqtt_topic_cmd[65];
    char mqtt_topic_dat[65];
    int  mqtt_keepalive;
    int  mqtt_reconnect_ms;

    // Sensor
    int  sensor_gain;
    int  sensor_integration;
    int  sensor_led_current;
    int  sensor_poll_ms;
    int  sensor_timeout_ms;

    // Nextion
    int  nextion_baud;
    int  nextion_tx;
    int  nextion_rx;

    // OTA
    char ota_url[256];

    // Timings
    int  training_interval_ms;
    int  deep_sleep_min;
} waterly_config_t;

// --- API PUBLICA ---

/**
 * @brief Inicializa el gestor de configuracion.
 * Carga desde NVS o escribe defaults si no existe.
 */
esp_err_t config_manager_init(void);

/**
 * @brief Obtiene la configuracion actual (copia).
 */
esp_err_t config_manager_get(waterly_config_t *config);

/**
 * @brief Guarda una configuracion en NVS.
 * Reinicia el dispositivo si reboot=true.
 */
esp_err_t config_manager_set(const waterly_config_t *config, bool reboot);

/**
 * @brief Actualiza un campo especifico en NVS.
 */
esp_err_t config_manager_set_wifi(const char *ssid, const char *pass);
esp_err_t config_manager_set_mqtt_broker(const char *ip);
esp_err_t config_manager_set_mqtt_topics(const char *cmd, const char *dat);
esp_err_t config_manager_set_sensor(int gain, int integration, int led_current);

/**
 * @brief Borra toda la configuracion de NVS (factory reset).
 */
esp_err_t config_manager_factory_reset(void);

/**
 * @brief Imprime la configuracion actual por el log.
 */
void config_manager_print(void);

/**
 * @brief Verifica si hay credenciales WiFi guardadas.
 */
bool config_manager_has_wifi_credentials(void);

#endif
