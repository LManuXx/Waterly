#include <string.h>
#include <esp_log.h>
#include <esp_wifi.h>
#include <esp_event.h>
#include <nvs_flash.h>
#include <wifi_provisioning/manager.h>
#include <wifi_provisioning/scheme_ble.h> 

#include "wifi_connect.h"

static const char *TAG = "WIFI_PROV";
static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

// --- NUEVO: Configuración de reintentos ---
static int s_retry_num = 0;
#define MAXIMUM_RETRY 5

/* Manejador de eventos WiFi e IP */
static void event_handler(void* arg, esp_event_base_t event_base,
                          int32_t event_id, void* event_data)
{
    // 1. Iniciar conexión
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } 
    // 2. Si se desconecta o falla la conexión
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        
        if (s_retry_num < MAXIMUM_RETRY) {
            // Aún nos quedan intentos... probamos otra vez
            esp_wifi_connect();
            s_retry_num++;
            ESP_LOGW(TAG, "Reintentando conexión al AP... (%d/%d)", s_retry_num, MAXIMUM_RETRY);
        } else {
            // Se acabaron los intentos. Asumimos que el WiFi cambió o es incorrecto.
            ESP_LOGE(TAG, "Fallo crítico: No se puede conectar al WiFi guardado.");
            ESP_LOGE(TAG, ">>> BORRANDO CREDENCIALES Y REINICIANDO EN MODO CONFIGURACION <<<");
            
            // Re-inicializamos el manager brevemente para poder ejecutar el comando de borrado
            wifi_prov_mgr_config_t config = {
                .scheme = wifi_prov_scheme_ble,
                .scheme_event_handler = WIFI_PROV_SCHEME_BLE_EVENT_HANDLER_FREE_BTDM
            };
            wifi_prov_mgr_init(config);
            
            // Esta función mágica borra el WiFi de la memoria Flash
            wifi_prov_mgr_reset_provisioning();
            
            // Reiniciamos para empezar limpio (entrará en modo Bluetooth al arrancar)
            esp_restart();
        }
    } 
    // 3. Conexión Exitosa
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "¡Conectado! IP:" IPSTR, IP2STR(&event->ip_info.ip));
        s_retry_num = 0; // Reseteamos contador por si se desconecta en el futuro
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void get_device_service_name(char *service_name, size_t max)
{
    uint8_t eth_mac[6];
    const char *ssid_prefix = "PROV_Waterly_";
    esp_wifi_get_mac(WIFI_IF_STA, eth_mac);
    snprintf(service_name, max, "%s%02X%02X%02X",
             ssid_prefix, eth_mac[3], eth_mac[4], eth_mac[5]);
}

esp_err_t wifi_connect_init(void)
{
    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL));

    wifi_prov_mgr_config_t config = {
        .scheme = wifi_prov_scheme_ble,
        .scheme_event_handler = WIFI_PROV_SCHEME_BLE_EVENT_HANDLER_FREE_BTDM
    };

    ESP_ERROR_CHECK(wifi_prov_mgr_init(config));

    bool provisioned = false;
    ESP_ERROR_CHECK(wifi_prov_mgr_is_provisioned(&provisioned));

    if (provisioned) {
        ESP_LOGI(TAG, "Credenciales encontradas. Intentando conectar...");
        wifi_prov_mgr_deinit(); // Liberamos RAM del Bluetooth
        esp_wifi_set_mode(WIFI_MODE_STA);
        esp_wifi_start();
    } else {
        ESP_LOGI(TAG, "Iniciando Aprovisionamiento (Bluetooth)...");
        
        char service_name[32];
        get_device_service_name(service_name, sizeof(service_name));

        wifi_prov_security_t security = WIFI_PROV_SECURITY_1;
        const char *pop = "waterly123"; 

        wifi_prov_mgr_start_provisioning(security, pop, service_name, NULL);
        
        ESP_LOGW(TAG, "ESTADO: Esperando configuración en App móvil (EspBleProv)");
    }

    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT, false, true, portMAX_DELAY);

    return ESP_OK;
}