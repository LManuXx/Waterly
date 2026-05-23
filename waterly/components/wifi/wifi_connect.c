#include <string.h>
#include <esp_log.h>
#include <esp_wifi.h>
#include <esp_event.h>
#include <nvs_flash.h>
#include <wifi_provisioning/manager.h>
#include <wifi_provisioning/scheme_ble.h>

#include "wifi_connect.h"
#include "nextion.h"
#include "config_manager.h"

static const char *TAG = "WIFI_PROV";
static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

static int s_retry_num = 0;

/* Manejador de eventos WiFi e IP */
static void event_handler(void* arg, esp_event_base_t event_base,
                          int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "WIFI_EVENT_STA_START - conectando...");
        esp_wifi_connect();
    }
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WIFI_EVENT_STA_DISCONNECTED");

        waterly_config_t cfg;
        config_manager_get(&cfg);
        int max_retry = cfg.wifi_retry_max;

        if (s_retry_num < max_retry) {
            esp_wifi_connect();
            s_retry_num++;
            ESP_LOGW(TAG, "Reintentando conexion al AP... (%d/%d)", s_retry_num, max_retry);
            char retry_msg[20];
            snprintf(retry_msg, sizeof(retry_msg), "WiFi %d/%d", s_retry_num, max_retry);
            nextion_send_txt("page0.t2", retry_msg);
        } else {
            ESP_LOGE(TAG, "Superados los reintentos (%d). Fallo de conexion.", max_retry);
            nextion_send_txt("page0.t2", "WiFi offline");
            s_retry_num = 0;
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
    }
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "IP_EVENT_STA_GOT_IP - ¡Conectado! IP:" IPSTR, IP2STR(&event->ip_info.ip));
        s_retry_num = 0;
        char ip_str[20];
        snprintf(ip_str, sizeof(ip_str), IPSTR, IP2STR(&event->ip_info.ip));
        nextion_send_txt("page0.t2", ip_str);
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

/* Handler de eventos de provisioning */
static void provisioning_event_handler(void* arg, esp_event_base_t event_base,
                                       int32_t event_id, void* event_data)
{
    if (event_base == WIFI_PROV_EVENT) {
        switch (event_id) {
            case WIFI_PROV_START:
                ESP_LOGI(TAG, "Provisioning iniciado");
                nextion_send_txt("page0.t2", "BLE activo...");
                break;
            case WIFI_PROV_CRED_RECV: {
                wifi_sta_config_t* wifi_sta_cfg = (wifi_sta_config_t*)event_data;
                ESP_LOGI(TAG, "Credenciales recibidas - SSID: %s", (const char*)wifi_sta_cfg->ssid);
                nextion_send_txt("page0.t2", "Creds OK");
                break;
            }
            case WIFI_PROV_CRED_FAIL: {
                wifi_prov_sta_fail_reason_t* reason = (wifi_prov_sta_fail_reason_t*)event_data;
                ESP_LOGE(TAG, "Provisioning fallo: %s",
                         (*reason == WIFI_PROV_STA_AUTH_ERROR) ? "Auth Error" : "AP Not Found");
                nextion_send_txt("page0.t2", "Cred FAIL");
                break;
            }
            case WIFI_PROV_CRED_SUCCESS:
                ESP_LOGI(TAG, "Provisioning exitoso - credenciales guardadas en NVS");
                nextion_send_txt("page0.t2", "Prov OK!");
                break;
            case WIFI_PROV_END:
                ESP_LOGI(TAG, "Provisioning finalizado");
                wifi_prov_mgr_deinit();
                break;
            default:
                break;
        }
    }
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

    // Registrar handler de eventos de provisioning
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_PROV_EVENT, ESP_EVENT_ANY_ID, &provisioning_event_handler, NULL));

    wifi_prov_mgr_config_t config = {
        .scheme = wifi_prov_scheme_ble,
        .scheme_event_handler = WIFI_PROV_SCHEME_BLE_EVENT_HANDLER_FREE_BTDM
    };

    ESP_ERROR_CHECK(wifi_prov_mgr_init(config));

    // 1. Verificar si hay credenciales de provisioning manager
    bool provisioned = false;
    ESP_ERROR_CHECK(wifi_prov_mgr_is_provisioned(&provisioned));
    ESP_LOGI(TAG, "wifi_prov_mgr_is_provisioned = %s", provisioned ? "SI" : "NO");

    // 2. Verificar si hay credenciales en config_manager
    bool has_cfg_creds = config_manager_has_wifi_credentials();
    ESP_LOGI(TAG, "config_manager_has_wifi_credentials = %s", has_cfg_creds ? "SI" : "NO");

    if (provisioned) {
        // Credenciales del provisioning manager (BLE anterior)
        ESP_LOGI(TAG, "Usando credenciales de provisioning manager");
        nextion_send_txt("page0.t2", "Conectando...");
        wifi_prov_mgr_deinit();
        esp_wifi_set_mode(WIFI_MODE_STA);
        esp_wifi_start();
    }
    else if (has_cfg_creds) {
        // Credenciales de config_manager (OTA config via MQTT)
        waterly_config_t nvs_cfg;
        config_manager_get(&nvs_cfg);
        
        ESP_LOGI(TAG, "Usando credenciales de config_manager - SSID: '%s'", nvs_cfg.wifi_ssid);
        nextion_send_txt("page0.t2", "Conectando...");
        
        wifi_prov_mgr_deinit();
        
        wifi_config_t wifi_cfg = {
            .sta = {
                .ssid = "",
                .password = "",
            },
        };
        strncpy((char*)wifi_cfg.sta.ssid, nvs_cfg.wifi_ssid, sizeof(wifi_cfg.sta.ssid) - 1);
        strncpy((char*)wifi_cfg.sta.password, nvs_cfg.wifi_pass, sizeof(wifi_cfg.sta.password) - 1);
        
        esp_wifi_set_mode(WIFI_MODE_STA);
        esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
        esp_wifi_start();
    }
    else {
        // No hay credenciales - iniciar BLE provisioning
        char service_name[32];
        get_device_service_name(service_name, sizeof(service_name));

        waterly_config_t nvs_cfg;
        config_manager_get(&nvs_cfg);

        wifi_prov_security_t security = WIFI_PROV_SECURITY_1;
        const char *pop = nvs_cfg.ble_pop;

        ESP_LOGI(TAG, "Iniciando BLE provisioning - POP: '%s', Service: '%s'", pop, service_name);
        wifi_prov_mgr_start_provisioning(security, pop, service_name, NULL);
        
        nextion_send_txt("page0.t2", "Config BLE...");
    }

    // Esperar a conexion o fallo
    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
                                           WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                           pdFALSE, pdFALSE, portMAX_DELAY);

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "WiFi conectado exitosamente");
        return ESP_OK;
    }
    else if (bits & WIFI_FAIL_BIT) {
        ESP_LOGE(TAG, "WiFi fallo de conexion");
        return ESP_FAIL;
    }

    ESP_LOGE(TAG, "Evento inesperado");
    return ESP_FAIL;
}
