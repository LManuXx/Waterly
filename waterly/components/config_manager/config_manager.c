#include <string.h>
#include <esp_log.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <nvs.h>
#include <nvs_flash.h>
#include "config_manager.h"

static const char *TAG = "CONFIG_MGR";
static waterly_config_t s_config;
static bool s_initialized = false;

// --- HELPERS ---

static esp_err_t nvs_get_str_or_default(nvs_handle_t handle, const char *key, char *out, size_t max_len, const char *default_val) {
    size_t len = max_len;
    esp_err_t err = nvs_get_str(handle, key, out, &len);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        strncpy(out, default_val, max_len - 1);
        out[max_len - 1] = '\0';
        return ESP_OK;
    }
    return err;
}

static esp_err_t nvs_get_int_or_default(nvs_handle_t handle, const char *key, int32_t *out, int32_t default_val) {
    esp_err_t err = nvs_get_i32(handle, key, out);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        *out = default_val;
        return ESP_OK;
    }
    return err;
}

// --- INICIALIZACION ---

esp_err_t config_manager_init(void) {
    if (s_initialized) {
        ESP_LOGW(TAG, "Ya inicializado");
        return ESP_OK;
    }

    // Inicializar NVS si no esta hecho
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS corrupto o sin espacio, borrando...");
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    nvs_handle_t nvs;
    ret = nvs_open(CONFIG_NAMESPACE, NVS_READONLY, &nvs);
    
    if (ret == ESP_ERR_NVS_NOT_FOUND) {
        // No existe configuracion, usar defaults
        ESP_LOGI(TAG, "No hay configuracion en NVS, usando defaults");
        
        strncpy(s_config.wifi_ssid, DEFAULT_WIFI_SSID, sizeof(s_config.wifi_ssid) - 1);
        strncpy(s_config.wifi_pass, DEFAULT_WIFI_PASS, sizeof(s_config.wifi_pass) - 1);
        s_config.wifi_retry_max = DEFAULT_WIFI_RETRY_MAX;
        
        strncpy(s_config.ble_pop, DEFAULT_BLE_POP, sizeof(s_config.ble_pop) - 1);
        
        strncpy(s_config.mqtt_broker_ip, DEFAULT_MQTT_BROKER_IP, sizeof(s_config.mqtt_broker_ip) - 1);
        strncpy(s_config.mqtt_hostname, DEFAULT_MQTT_HOSTNAME, sizeof(s_config.mqtt_hostname) - 1);
        strncpy(s_config.mqtt_client_id, DEFAULT_MQTT_CLIENT_ID, sizeof(s_config.mqtt_client_id) - 1);
        strncpy(s_config.mqtt_topic_cmd, DEFAULT_MQTT_TOPIC_CMD, sizeof(s_config.mqtt_topic_cmd) - 1);
        strncpy(s_config.mqtt_topic_dat, DEFAULT_MQTT_TOPIC_DAT, sizeof(s_config.mqtt_topic_dat) - 1);
        s_config.mqtt_keepalive = DEFAULT_MQTT_KEEPALIVE;
        s_config.mqtt_reconnect_ms = DEFAULT_MQTT_RECONNECT_MS;
        
        s_config.sensor_gain = DEFAULT_SENSOR_GAIN;
        s_config.sensor_integration = DEFAULT_SENSOR_INTEGRATION;
        s_config.sensor_led_current = DEFAULT_SENSOR_LED_CURRENT;
        s_config.sensor_poll_ms = DEFAULT_SENSOR_POLL_MS;
        s_config.sensor_timeout_ms = DEFAULT_SENSOR_TIMEOUT_MS;
        
        s_config.nextion_baud = DEFAULT_NEXTION_BAUD;
        s_config.nextion_tx = DEFAULT_NEXTION_TX;
        s_config.nextion_rx = DEFAULT_NEXTION_RX;
        
        strncpy(s_config.ota_url, DEFAULT_OTA_URL, sizeof(s_config.ota_url) - 1);
        
        s_config.training_interval_ms = DEFAULT_TRAINING_INTERVAL_MS;
        s_config.deep_sleep_min = DEFAULT_DEEP_SLEEP_MIN;
        
        // Guardar defaults en NVS
        config_manager_set(&s_config, false);
        
    } else {
        ESP_ERROR_CHECK(ret);
        
        // Leer desde NVS
        nvs_get_str_or_default(nvs, "wifi_ssid", s_config.wifi_ssid, sizeof(s_config.wifi_ssid), DEFAULT_WIFI_SSID);
        nvs_get_str_or_default(nvs, "wifi_pass", s_config.wifi_pass, sizeof(s_config.wifi_pass), DEFAULT_WIFI_PASS);
        nvs_get_int_or_default(nvs, "wifi_retry_max", (int32_t*)&s_config.wifi_retry_max, DEFAULT_WIFI_RETRY_MAX);
        
        nvs_get_str_or_default(nvs, "ble_pop", s_config.ble_pop, sizeof(s_config.ble_pop), DEFAULT_BLE_POP);
        
        nvs_get_str_or_default(nvs, "mqtt_broker_ip", s_config.mqtt_broker_ip, sizeof(s_config.mqtt_broker_ip), DEFAULT_MQTT_BROKER_IP);
        nvs_get_str_or_default(nvs, "mqtt_hostname", s_config.mqtt_hostname, sizeof(s_config.mqtt_hostname), DEFAULT_MQTT_HOSTNAME);
        nvs_get_str_or_default(nvs, "mqtt_client_id", s_config.mqtt_client_id, sizeof(s_config.mqtt_client_id), DEFAULT_MQTT_CLIENT_ID);
        nvs_get_str_or_default(nvs, "mqtt_topic_cmd", s_config.mqtt_topic_cmd, sizeof(s_config.mqtt_topic_cmd), DEFAULT_MQTT_TOPIC_CMD);
        nvs_get_str_or_default(nvs, "mqtt_topic_dat", s_config.mqtt_topic_dat, sizeof(s_config.mqtt_topic_dat), DEFAULT_MQTT_TOPIC_DAT);
        nvs_get_int_or_default(nvs, "mqtt_keepalive", (int32_t*)&s_config.mqtt_keepalive, DEFAULT_MQTT_KEEPALIVE);
        nvs_get_int_or_default(nvs, "mqtt_reconnect_ms", (int32_t*)&s_config.mqtt_reconnect_ms, DEFAULT_MQTT_RECONNECT_MS);
        
        nvs_get_int_or_default(nvs, "sensor_gain", (int32_t*)&s_config.sensor_gain, DEFAULT_SENSOR_GAIN);
        nvs_get_int_or_default(nvs, "sensor_integration", (int32_t*)&s_config.sensor_integration, DEFAULT_SENSOR_INTEGRATION);
        nvs_get_int_or_default(nvs, "sensor_led_current", (int32_t*)&s_config.sensor_led_current, DEFAULT_SENSOR_LED_CURRENT);
        nvs_get_int_or_default(nvs, "sensor_poll_ms", (int32_t*)&s_config.sensor_poll_ms, DEFAULT_SENSOR_POLL_MS);
        nvs_get_int_or_default(nvs, "sensor_timeout_ms", (int32_t*)&s_config.sensor_timeout_ms, DEFAULT_SENSOR_TIMEOUT_MS);
        
        nvs_get_int_or_default(nvs, "nextion_baud", (int32_t*)&s_config.nextion_baud, DEFAULT_NEXTION_BAUD);
        nvs_get_int_or_default(nvs, "nextion_tx", (int32_t*)&s_config.nextion_tx, DEFAULT_NEXTION_TX);
        nvs_get_int_or_default(nvs, "nextion_rx", (int32_t*)&s_config.nextion_rx, DEFAULT_NEXTION_RX);
        
        nvs_get_str_or_default(nvs, "ota_url", s_config.ota_url, sizeof(s_config.ota_url), DEFAULT_OTA_URL);
        
        nvs_get_int_or_default(nvs, "training_interval_ms", (int32_t*)&s_config.training_interval_ms, DEFAULT_TRAINING_INTERVAL_MS);
        nvs_get_int_or_default(nvs, "deep_sleep_min", (int32_t*)&s_config.deep_sleep_min, DEFAULT_DEEP_SLEEP_MIN);
        
        nvs_close(nvs);
        ESP_LOGI(TAG, "Configuracion cargada desde NVS");
    }
    
    s_initialized = true;
    config_manager_print();
    return ESP_OK;
}

// --- GETTERS ---

esp_err_t config_manager_get(waterly_config_t *config) {
    if (!s_initialized) return ESP_ERR_INVALID_STATE;
    if (!config) return ESP_ERR_INVALID_ARG;
    
    memcpy(config, &s_config, sizeof(waterly_config_t));
    return ESP_OK;
}

bool config_manager_has_wifi_credentials(void) {
    return s_initialized && strlen(s_config.wifi_ssid) > 0;
}

// --- SETTERS ---

esp_err_t config_manager_set(const waterly_config_t *config, bool reboot) {
    if (!config) return ESP_ERR_INVALID_ARG;
    
    nvs_handle_t nvs;
    esp_err_t ret = nvs_open(CONFIG_NAMESPACE, NVS_READWRITE, &nvs);
    if (ret != ESP_OK) return ret;
    
    nvs_set_str(nvs, "wifi_ssid", config->wifi_ssid);
    nvs_set_str(nvs, "wifi_pass", config->wifi_pass);
    nvs_set_i32(nvs, "wifi_retry_max", config->wifi_retry_max);
    
    nvs_set_str(nvs, "ble_pop", config->ble_pop);
    
    nvs_set_str(nvs, "mqtt_broker_ip", config->mqtt_broker_ip);
    nvs_set_str(nvs, "mqtt_hostname", config->mqtt_hostname);
    nvs_set_str(nvs, "mqtt_client_id", config->mqtt_client_id);
    nvs_set_str(nvs, "mqtt_topic_cmd", config->mqtt_topic_cmd);
    nvs_set_str(nvs, "mqtt_topic_dat", config->mqtt_topic_dat);
    nvs_set_i32(nvs, "mqtt_keepalive", config->mqtt_keepalive);
    nvs_set_i32(nvs, "mqtt_reconnect_ms", config->mqtt_reconnect_ms);
    
    nvs_set_i32(nvs, "sensor_gain", config->sensor_gain);
    nvs_set_i32(nvs, "sensor_integration", config->sensor_integration);
    nvs_set_i32(nvs, "sensor_led_current", config->sensor_led_current);
    nvs_set_i32(nvs, "sensor_poll_ms", config->sensor_poll_ms);
    nvs_set_i32(nvs, "sensor_timeout_ms", config->sensor_timeout_ms);
    
    nvs_set_i32(nvs, "nextion_baud", config->nextion_baud);
    nvs_set_i32(nvs, "nextion_tx", config->nextion_tx);
    nvs_set_i32(nvs, "nextion_rx", config->nextion_rx);
    
    nvs_set_str(nvs, "ota_url", config->ota_url);
    
    nvs_set_i32(nvs, "training_interval_ms", config->training_interval_ms);
    nvs_set_i32(nvs, "deep_sleep_min", config->deep_sleep_min);
    
    ret = nvs_commit(nvs);
    nvs_close(nvs);
    
    if (ret == ESP_OK) {
        memcpy(&s_config, config, sizeof(waterly_config_t));
        ESP_LOGI(TAG, "Configuracion guardada en NVS");
        
        if (reboot) {
            ESP_LOGW(TAG, "Reiniciando en 2s...");
            vTaskDelay(pdMS_TO_TICKS(2000));
            esp_restart();
        }
    }
    
    return ret;
}

esp_err_t config_manager_set_wifi(const char *ssid, const char *pass) {
    if (!ssid || strlen(ssid) == 0) return ESP_ERR_INVALID_ARG;
    
    waterly_config_t cfg;
    config_manager_get(&cfg);
    
    strncpy(cfg.wifi_ssid, ssid, sizeof(cfg.wifi_ssid) - 1);
    cfg.wifi_ssid[sizeof(cfg.wifi_ssid) - 1] = '\0';
    
    if (pass) {
        strncpy(cfg.wifi_pass, pass, sizeof(cfg.wifi_pass) - 1);
        cfg.wifi_pass[sizeof(cfg.wifi_pass) - 1] = '\0';
    }
    
    return config_manager_set(&cfg, false);
}

esp_err_t config_manager_set_mqtt_broker(const char *ip) {
    if (!ip || strlen(ip) == 0) return ESP_ERR_INVALID_ARG;
    
    waterly_config_t cfg;
    config_manager_get(&cfg);
    
    strncpy(cfg.mqtt_broker_ip, ip, sizeof(cfg.mqtt_broker_ip) - 1);
    cfg.mqtt_broker_ip[sizeof(cfg.mqtt_broker_ip) - 1] = '\0';
    
    return config_manager_set(&cfg, false);
}

esp_err_t config_manager_set_mqtt_topics(const char *cmd, const char *dat) {
    if (!cmd || !dat) return ESP_ERR_INVALID_ARG;
    
    waterly_config_t cfg;
    config_manager_get(&cfg);
    
    strncpy(cfg.mqtt_topic_cmd, cmd, sizeof(cfg.mqtt_topic_cmd) - 1);
    cfg.mqtt_topic_cmd[sizeof(cfg.mqtt_topic_cmd) - 1] = '\0';
    
    strncpy(cfg.mqtt_topic_dat, dat, sizeof(cfg.mqtt_topic_dat) - 1);
    cfg.mqtt_topic_dat[sizeof(cfg.mqtt_topic_dat) - 1] = '\0';
    
    return config_manager_set(&cfg, false);
}

esp_err_t config_manager_set_sensor(int gain, int integration, int led_current) {
    waterly_config_t cfg;
    config_manager_get(&cfg);
    
    cfg.sensor_gain = gain;
    cfg.sensor_integration = integration;
    cfg.sensor_led_current = led_current;
    
    return config_manager_set(&cfg, false);
}

// --- FACTORY RESET ---

esp_err_t config_manager_factory_reset(void) {
    ESP_LOGW(TAG, "FACTORY RESET: Borrando NVS...");
    esp_err_t ret = nvs_flash_erase();
    if (ret == ESP_OK) {
        ESP_LOGW(TAG, "Reiniciando en 2s...");
        vTaskDelay(pdMS_TO_TICKS(2000));
        esp_restart();
    }
    return ret;
}

// --- DEBUG ---

void config_manager_print(void) {
    if (!s_initialized) return;
    
    ESP_LOGI(TAG, "=== CONFIGURACION WATERLY ===");
    ESP_LOGI(TAG, "WiFi SSID: '%s'", s_config.wifi_ssid[0] ? s_config.wifi_ssid : "(vacio)");
    ESP_LOGI(TAG, "WiFi Retry: %d", s_config.wifi_retry_max);
    ESP_LOGI(TAG, "BLE POP: '%s'", s_config.ble_pop);
    ESP_LOGI(TAG, "MQTT Broker: %s", s_config.mqtt_broker_ip);
    ESP_LOGI(TAG, "MQTT Hostname: %s", s_config.mqtt_hostname);
    ESP_LOGI(TAG, "MQTT Client ID: %s", s_config.mqtt_client_id);
    ESP_LOGI(TAG, "MQTT Topics: cmd='%s' dat='%s'", s_config.mqtt_topic_cmd, s_config.mqtt_topic_dat);
    ESP_LOGI(TAG, "Sensor: gain=%d integration=%d led=%d", 
             s_config.sensor_gain, s_config.sensor_integration, s_config.sensor_led_current);
    ESP_LOGI(TAG, "Nextion: baud=%d tx=%d rx=%d", 
             s_config.nextion_baud, s_config.nextion_tx, s_config.nextion_rx);
    ESP_LOGI(TAG, "Training interval: %dms", s_config.training_interval_ms);
    ESP_LOGI(TAG, "===========================");
}
