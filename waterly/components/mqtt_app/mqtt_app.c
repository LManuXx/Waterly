#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "mqtt_app.h"
#include "mdns.h"
#include "nextion.h"

// IMPRESCINDIBLE: Incluir el controlador para enviar eventos
#include "app_controller.h" 

static const char *TAG = "MQTT_APP";
esp_mqtt_client_handle_t client = NULL;

#define MDNS_TARGET_HOSTNAME "waterly" 
#define MQTT_PORT 1883

// --- CONFIG DE REINTENTOS ---
#define MDNS_MAX_RETRIES     5      // Intentos de búsqueda mDNS
#define MDNS_TIMEOUT_MS      3000   // Timeout por intento (3s)
#define MDNS_RETRY_DELAY_MS  2000   // Pausa entre reintentos (2s)

char mqtt_uri_buffer[64]; 

static char* resolve_mdns_host(const char * host_name) {
    ESP_LOGI(TAG, "Iniciando mDNS para buscar: %s.local", host_name);
    
    ESP_ERROR_CHECK(mdns_init());
    mdns_hostname_set("waterly-sensor"); // Nombre del ESP32 en la red

    esp_ip4_addr_t addr;
    
    // Reintentamos varias veces por si el servidor aún no ha arrancado
    for (int intento = 1; intento <= MDNS_MAX_RETRIES; intento++) {
        addr.addr = 0;
        
        char msg[24];
        snprintf(msg, sizeof(msg), "mDNS %d/%d...", intento, MDNS_MAX_RETRIES);
        nextion_send_txt("page0.t0", msg);
        
        ESP_LOGI(TAG, "Buscando '%s.local' (intento %d/%d)...", host_name, intento, MDNS_MAX_RETRIES);
        esp_err_t err = mdns_query_a(host_name, MDNS_TIMEOUT_MS, &addr);
        
        if (err == ESP_OK && addr.addr != 0) {
            static char ip_str[16];
            snprintf(ip_str, sizeof(ip_str), IPSTR, IP2STR(&addr));
            ESP_LOGI(TAG, "Servidor encontrado en: %s", ip_str);
            nextion_send_txt("page0.t0", "Servidor OK");
            mdns_free();
            return ip_str;
        }
        
        if (intento < MDNS_MAX_RETRIES) {
            ESP_LOGW(TAG, "No encontrado, reintentando en %dms...", MDNS_RETRY_DELAY_MS);
            vTaskDelay(pdMS_TO_TICKS(MDNS_RETRY_DELAY_MS));
        }
    }

    ESP_LOGW(TAG, "No encontré '%s.local' tras %d intentos.", host_name, MDNS_MAX_RETRIES);
    nextion_send_txt("page0.t0", "mDNS fallo");
    mdns_free();
    return NULL;
}

static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    esp_mqtt_event_handle_t event = event_data;
    
    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT Conectado! Suscribiendo...");
        nextion_send_txt("page0.t0", "MQTT OK");
        esp_mqtt_client_subscribe(client, "waterly/comandos", 0);
        break;

    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT Desconectado. Reconectando automaticamente...");
        nextion_send_txt("page0.t0", "MQTT recon...");
        // El cliente ESP-IDF MQTT reconecta automáticamente, no hace falta hacer nada
        break;

    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "Error MQTT de transporte");
        nextion_send_txt("page0.t0", "MQTT error");
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

    default: 
        break;
    }
}

void mqtt_app_start(void) {
    // 1. INTENTAR AUTODETECTAR SERVIDOR POR mDNS (5 intentos)
    nextion_send_txt("page0.t0", "Buscando srv...");
    char *server_ip = resolve_mdns_host(MDNS_TARGET_HOSTNAME);
    
    if (server_ip != NULL) {
        // mDNS encontró el servidor automáticamente
        snprintf(mqtt_uri_buffer, sizeof(mqtt_uri_buffer), "mqtt://%s:%d", server_ip, MQTT_PORT);
    } else {
        // FALLBACK: IP fija de emergencia (último recurso)
        ESP_LOGE(TAG, "Fallo autodescubrimiento. Usando IP fija de emergencia.");
        nextion_send_txt("page0.t0", "IP fija...");
        snprintf(mqtt_uri_buffer, sizeof(mqtt_uri_buffer), "mqtt://192.168.50.136:1883");
    }

    ESP_LOGI(TAG, "Conectando al Broker: %s", mqtt_uri_buffer);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = mqtt_uri_buffer,
        .session.keepalive = 120,                 // Ping cada 120s (Mosquitto tiene timeout 1.5x = 180s)
        .network.reconnect_timeout_ms = 5000,     // Reconectar cada 5s si se cae
        .credentials.set_null_client_id = false,   // Usar client_id explícito
        .credentials.client_id = "waterly_esp32",  // ID único para evitar conflictos
    };
    
    client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, client);
    esp_mqtt_client_start(client);
}

void mqtt_app_send_full_spectrum(as7265x_values_t *vals) {
    if (client == NULL) {
        ESP_LOGE(TAG, "Cliente MQTT no conectado, no se pueden enviar datos.");
        return;
    }

    char payload[512];
    int len = snprintf(payload, sizeof(payload),
        "{\"A_410nm\":%.2f,\"B_435nm\":%.2f,\"C_460nm\":%.2f,\"D_485nm\":%.2f,\"E_510nm\":%.2f,\"F_535nm\":%.2f,"
        "\"G_560nm\":%.2f,\"H_585nm\":%.2f,\"I_645nm\":%.2f,\"J_705nm\":%.2f,\"K_900nm\":%.2f,\"L_940nm\":%.2f,"
        "\"R_610nm\":%.2f,\"S_680nm\":%.2f,\"T_730nm\":%.2f,\"U_760nm\":%.2f,\"V_810nm\":%.2f,\"W_860nm\":%.2f}",
        vals->A, vals->B, vals->C, vals->D, vals->E, vals->F,
        vals->G, vals->H, vals->I, vals->J, vals->K, vals->L,
        vals->R, vals->S, vals->T, vals->U, vals->V, vals->W);

    if (len > 0 && len < sizeof(payload)) {
        esp_mqtt_client_publish(client, "waterly/datos", payload, 0, 1, 0);
        ESP_LOGI(TAG, "Datos espectrales enviados (%d bytes)", len);
    } else {
        ESP_LOGE(TAG, "Payload demasiado largo o error en snprintf");
    }
}