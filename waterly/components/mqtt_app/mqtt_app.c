#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "mqtt_app.h"

// IMPRESCINDIBLE: Incluir el controlador para enviar eventos
#include "app_controller.h" 

static const char *TAG = "MQTT_APP";
esp_mqtt_client_handle_t client = NULL;

// CAMBIA ESTO POR TU IP
#define MQTT_BROKER_URI "mqtt://192.168.1.25:1883" 

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
    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = MQTT_BROKER_URI,
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