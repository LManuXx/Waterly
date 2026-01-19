#include <string.h>
#include <esp_log.h>
#include <esp_wifi.h>
#include <esp_event.h>
#include <nvs_flash.h>
#include <wifi_provisioning/manager.h>
#include <wifi_provisioning/scheme_ble.h> // Usaremos Bluetooth LE

#include "wifi_connect.h"

static const char *TAG = "WIFI_PROV";

// Event Group para esperar la IP
static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

/* Manejador de eventos WiFi e IP */
static void event_handler(void* arg, esp_event_base_t event_base,
                          int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } 
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGI(TAG, "Desconectado. Reintentando conectar...");
        esp_wifi_connect();
    } 
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "¡Conectado! IP:" IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

/* Manejador de eventos de Aprovisionamiento (Para logs) */
static void get_device_service_name(char *service_name, size_t max)
{
    // Nombre del dispositivo en el Bluetooth: "PROV_Waterly"
    uint8_t eth_mac[6];
    const char *ssid_prefix = "PROV_Waterly_";
    esp_wifi_get_mac(WIFI_IF_STA, eth_mac);
    snprintf(service_name, max, "%s%02X%02X%02X",
             ssid_prefix, eth_mac[3], eth_mac[4], eth_mac[5]);
}

esp_err_t wifi_connect_init(void)
{
    s_wifi_event_group = xEventGroupCreate();

    // 1. Inicializar Red
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    // 2. Registrar manejadores básicos
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL));

    // 3. Configuración del Provisioning Manager
    wifi_prov_mgr_config_t config = {
        .scheme = wifi_prov_scheme_ble, // Usar BLE (Bluetooth)
        .scheme_event_handler = WIFI_PROV_SCHEME_BLE_EVENT_HANDLER_FREE_BTDM
    };

    ESP_ERROR_CHECK(wifi_prov_mgr_init(config));

    // 4. Comprobar si ya tenemos WiFi guardado en flash
    bool provisioned = false;
    ESP_ERROR_CHECK(wifi_prov_mgr_is_provisioned(&provisioned));

    if (provisioned) {
        ESP_LOGI(TAG, "Ya aprovisionado. Conectando al WiFi guardado...");
        wifi_prov_mgr_deinit(); // Liberar memoria del manager
        esp_wifi_set_mode(WIFI_MODE_STA);
        esp_wifi_start();
    } else {
        ESP_LOGI(TAG, "No hay credenciales. Iniciando modo Aprovisionamiento (BLE)...");
        
        // Nombre del servicio Bluetooth
        char service_name[32];
        get_device_service_name(service_name, sizeof(service_name));

        // Seguridad: Pop of Proof (Contraseña para el Bluetooth)
        // NULL = Sin seguridad (para desarrollo)
        // "password" = Seguridad (recomendado para prod)
        wifi_prov_security_t security = WIFI_PROV_SECURITY_1;
        const char *pop = "waterly123"; // <--- CLAVE PARA LA APP MÓVIL, no necesitamos que sea sgura o desconocida, solo es para configurar el esp

        // Iniciar servicio de aprovisionamiento
        wifi_prov_mgr_start_provisioning(security, pop, service_name, NULL);
        
        ESP_LOGW(TAG, "ESTADO: Esperando configuración desde App Móvil...");
        ESP_LOGW(TAG, "Nombre Bluetooth: %s", service_name);
        ESP_LOGW(TAG, "Proof of Possession (POP): %s", pop);
    }

    // 5. Esperar a tener IP (Bloqueante)
    // Si no estamos configurados, esto esperará hasta que el usuario use la App.
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT, false, true, portMAX_DELAY);

    return ESP_OK;
}