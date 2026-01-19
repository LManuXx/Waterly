#include "ota_update.h"
#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_crt_bundle.h"
#include "cJSON.h"

static const char *TAG = "OTA";

// Buffer para guardar el JSON descargado
#define JSON_BUFFER_SIZE 512

// Función interna para descargar el firmware (la que ya tenías, ligeramente modificada)
static esp_err_t _run_ota_download(const char *bin_url)
{
    ESP_LOGI(TAG, "Iniciando descarga de Firmware desde: %s", bin_url);

    esp_http_client_config_t config = {
        .url = bin_url,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .keep_alive_enable = true,
        .timeout_ms = 10000,
        .buffer_size = 4096,
        .buffer_size_tx = 1024,
    };

    esp_https_ota_config_t ota_config = {
        .http_config = &config,
    };

    esp_err_t ret = esp_https_ota(&ota_config);
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "OTA Exitosa. Reiniciando...");
        esp_restart();
    } else {
        ESP_LOGE(TAG, "Fallo en OTA.");
    }
    return ret;
}

// Nueva función principal: Chequea JSON primero
esp_err_t check_and_update_firmware(const char *json_url, int current_version)
{
    ESP_LOGI(TAG, "Verificando actualizaciones en: %s", json_url);

    // 1. Configurar cliente HTTP para leer el JSON
    esp_http_client_config_t config = {
        .url = json_url,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 5000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);

    // 2. Descargar el JSON a un buffer
    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error conectando al servidor de versiones: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    int content_length = esp_http_client_fetch_headers(client);
    if (content_length <= 0) {
        ESP_LOGE(TAG, "El servidor envió una respuesta vacía");
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    char *buffer = malloc(JSON_BUFFER_SIZE);
    int read_len = esp_http_client_read(client, buffer, JSON_BUFFER_SIZE - 1);
    buffer[read_len] = '\0'; // Asegurar null-termination
    
    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    // 3. Parsear el JSON
    ESP_LOGI(TAG, "JSON recibido: %s", buffer);
    cJSON *json = cJSON_Parse(buffer);
    if (json == NULL) {
        ESP_LOGE(TAG, "El JSON no es válido");
        free(buffer);
        return ESP_FAIL;
    }

    // Extraer datos
    cJSON *ver_item = cJSON_GetObjectItem(json, "version");
    cJSON *url_item = cJSON_GetObjectItem(json, "url");

    if (!cJSON_IsNumber(ver_item) || !cJSON_IsString(url_item)) {
        ESP_LOGE(TAG, "Formato JSON incorrecto (falta 'version' o 'url')");
        cJSON_Delete(json);
        free(buffer);
        return ESP_FAIL;
    }

    int new_version = ver_item->valueint;
    char *new_url = strdup(url_item->valuestring); // Copiar URL

    // Limpiar memoria JSON
    cJSON_Delete(json);
    free(buffer);

    // 4. Comparar versiones
    ESP_LOGI(TAG, "Versión Actual: %d | Versión Nueva: %d", current_version, new_version);

    if (new_version > current_version) {
        ESP_LOGW(TAG, "¡Nueva versión detectada! Iniciando actualización...");
        // Llamar a la función de descarga con la URL que venía en el JSON
        _run_ota_download(new_url);
    } else {
        ESP_LOGI(TAG, "El sistema está actualizado.");
    }

    free(new_url);
    return ESP_OK;
}