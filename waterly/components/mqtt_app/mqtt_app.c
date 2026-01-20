#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "mqtt_app.h"

static const char *TAG = "MQTT_APP";
esp_mqtt_client_handle_t client = NULL;

bool modo_entrenamiento_activo = true;

// --- CONFIGURACIÓN ---
#define MQTT_BROKER_URI "mqtt://192.168.1.25:1883" 

#define TOPIC_DATA      "waterly/datos"
#define TOPIC_CMD       "waterly/comandos"

// Manejador de eventos
static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    esp_mqtt_event_handle_t event = event_data;
    
    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "Conectado! Suscribiendo a comandos...");
        // Nos suscribimos al canal de órdenes
        esp_mqtt_client_subscribe(client, TOPIC_CMD, 0);
        break;

    case MQTT_EVENT_DATA:
        ESP_LOGI(TAG, "Orden recibida en: %.*s", event->topic_len, event->topic);
        
        // Parsear el JSON recibido: {"training": true} o {"training": false}
        cJSON *root = cJSON_Parse(event->data);
        if (root) {
            cJSON *item = cJSON_GetObjectItem(root, "training");
            if (cJSON_IsBool(item)) {
                modo_entrenamiento_activo = cJSON_IsTrue(item);
                ESP_LOGW(TAG, "CAMBIO DE MODO: Entrenamiento = %s", modo_entrenamiento_activo ? "ON" : "OFF");
            }
            cJSON_Delete(root);
        }
        break;

    default:
        break;
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

    // Crear JSON: {"uv": 100, "vis": 200, "nir": 300}
    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "uv", uv);
    cJSON_AddNumberToObject(root, "vis", vis);
    cJSON_AddNumberToObject(root, "nir", nir);
    
    char *post_data = cJSON_PrintUnformatted(root);
    
    // Publicar al topic
    esp_mqtt_client_publish(client, TOPIC_DATA, post_data, 0, 0, 0);
    
    // Limpiar memoria
    cJSON_Delete(root);
    free(post_data);
}