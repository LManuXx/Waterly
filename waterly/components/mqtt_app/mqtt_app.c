#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "mqtt_app.h"
#include "mdns.h"

// IMPRESCINDIBLE: Incluir el controlador para enviar eventos
#include "app_controller.h" 

static const char *TAG = "MQTT_APP";
esp_mqtt_client_handle_t client = NULL;

#define MDNS_TARGET_HOSTNAME "waterly" 
#define MQTT_PORT 1883

char mqtt_uri_buffer[64]; 

static char* resolve_mdns_host(const char * host_name) {
    ESP_LOGI(TAG, "Iniciando mDNS para buscar: %s.local", host_name);
    
    ESP_ERROR_CHECK(mdns_init());
    mdns_hostname_set("waterly-sensor"); // Nombre del ESP32 en la red

    esp_ip4_addr_t addr;
    addr.addr = 0;

    ESP_LOGI(TAG, "Escaneando red... (Espere 3s)");
    // Buscamos la IP asociada al nombre "waterly"
    esp_err_t err = mdns_query_a(host_name, 3000, &addr);
    
    if(err) {
        if(err == ESP_ERR_NOT_FOUND) {
            ESP_LOGW(TAG, "No encontré 'waterly.local'.");
            return NULL;
        }
        ESP_LOGE(TAG, "Error mDNS: %d", err);
        return NULL;
    }

    static char ip_str[16];
    snprintf(ip_str, sizeof(ip_str), IPSTR, IP2STR(&addr));
    ESP_LOGI(TAG, "¡EUREKA! Servidor encontrado en: %s", ip_str);
    
    mdns_free(); // Ya tenemos la IP, cerramos mDNS
    return ip_str;
}

static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    esp_mqtt_event_handle_t event = event_data;
    
    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "Conectado! Suscribiendo...");
        esp_mqtt_client_subscribe(client, "waterly/comandos", 0);
        break;

    case MQTT_EVENT_DATA:
    ESP_LOGI(TAG, "Mensaje MQTT recibido");
    
    cJSON *root = cJSON_Parse(event->data);
    if (root) {
        
        // 1. CONTROL DE MODOS
        cJSON *item_mode = cJSON_GetObjectItem(root, "mode");
        if (cJSON_IsString(item_mode)) {
            const char* mode = item_mode->valuestring;
            
            if (strcmp(mode, "idle") == 0) {
                app_controller_send_event(APP_EVENT_GO_IDLE);
            } 
            else if (strcmp(mode, "single") == 0) {
                app_controller_send_event(APP_EVENT_SINGLE_MEASURE);
            } 
            else if (strcmp(mode, "training") == 0) {
                app_controller_send_event(APP_EVENT_START_TRAINING);
            } 
            else if (strcmp(mode, "sleep") == 0) {
                app_controller_send_event(APP_EVENT_STOP_AND_SLEEP);
            }
        }

        // 2. CONTROL DE OTA (Independiente)
        cJSON *item_update = cJSON_GetObjectItem(root, "update");
        if (cJSON_IsBool(item_update) && cJSON_IsTrue(item_update)) {
            app_controller_send_event(APP_EVENT_START_OTA);
        }

        cJSON_Delete(root);
    }
        break;
    default: break;
    }
}

void mqtt_app_start(void) {
    // 1. INTENTAR AUTODETECTAR SERVIDOR
    char *server_ip = resolve_mdns_host(MDNS_TARGET_HOSTNAME);
    
    if (server_ip != NULL) {
        // Si lo encontramos, usamos esa IP
        snprintf(mqtt_uri_buffer, sizeof(mqtt_uri_buffer), "mqtt://%s:%d", server_ip, MQTT_PORT);
    } else {
        // FALLBACK: Si falla, usamos la IP fija de emergencia (Cámbiala a la tuya actual)
        ESP_LOGE(TAG, "Fallo autodescubrimiento. Usando IP fija de emergencia.");
        snprintf(mqtt_uri_buffer, sizeof(mqtt_uri_buffer), "mqtt://192.168.1.25:1883"); 
    }

    ESP_LOGI(TAG, "Conectando al Broker: %s", mqtt_uri_buffer);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = mqtt_uri_buffer,
    };
    
    client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, client);
    esp_mqtt_client_start(client);
}

void mqtt_app_send_data(int uv, int vis, int nir) {
    if (client == NULL) return;
    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "uv", uv);
    cJSON_AddNumberToObject(root, "vis", vis);
    cJSON_AddNumberToObject(root, "nir", nir);
    char *post_data = cJSON_PrintUnformatted(root);
    esp_mqtt_client_publish(client, "waterly/datos", post_data, 0, 0, 0);
    cJSON_Delete(root);
    free(post_data);
}