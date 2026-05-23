#include "ota_update.h"
#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "cJSON.h"

static const char *TAG = "OTA";

#define JSON_BUFFER_SIZE 512
#define OTA_RECV_BUFFER 4096

static esp_err_t _run_ota_download(const char *bin_url)
{
    ESP_LOGI(TAG, "Descargando firmware desde: %s", bin_url);

    esp_http_client_config_t http_config = {
        .url = bin_url,
        .timeout_ms = 30000,
        .buffer_size = OTA_RECV_BUFFER,
        .disable_auto_redirect = false,
    };

    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error conectando al servidor de firmware: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    int content_length = esp_http_client_fetch_headers(client);
    if (content_length <= 0) {
        ESP_LOGE(TAG, "Error obteniendo headers HTTP (content_length=%d)", content_length);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Tamano del firmware: %d bytes", content_length);

    const esp_partition_t *update_partition = esp_ota_get_next_update_partition(NULL);
    if (update_partition == NULL) {
        ESP_LOGE(TAG, "No se encontro particion OTA disponible");
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Escribiendo en particion: %s (offset=0x%x, size=0x%x)",
             update_partition->label, update_partition->address, update_partition->size);

    esp_ota_handle_t ota_handle = 0;
    err = esp_ota_begin(update_partition, OTA_SIZE_UNKNOWN, &ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error iniciando OTA: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    char *recv_buf = malloc(OTA_RECV_BUFFER);
    if (recv_buf == NULL) {
        ESP_LOGE(TAG, "No hay memoria para buffer de recepcion");
        esp_ota_abort(ota_handle);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    int total_read = 0;
    int last_progress = 0;

    while (1) {
        int read_len = esp_http_client_read(client, recv_buf, OTA_RECV_BUFFER);
        if (read_len < 0) {
            ESP_LOGE(TAG, "Error leyendo datos HTTP: %s", esp_err_to_name(read_len));
            break;
        }
        if (read_len == 0) {
            break;
        }

        err = esp_ota_write(ota_handle, recv_buf, read_len);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Error escribiendo OTA: %s", esp_err_to_name(err));
            break;
        }

        total_read += read_len;
        int progress = (total_read * 100) / content_length;
        if (progress - last_progress >= 10) {
            ESP_LOGI(TAG, "Progreso OTA: %d%% (%d/%d bytes)", progress, total_read, content_length);
            last_progress = progress;
        }
    }

    free(recv_buf);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    if (total_read != content_length) {
        ESP_LOGE(TAG, "Tamano recibido (%d) != esperado (%d)", total_read, content_length);
        esp_ota_abort(ota_handle);
        return ESP_FAIL;
    }

    err = esp_ota_end(ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error finalizando OTA: %s", esp_err_to_name(err));
        return ESP_FAIL;
    }

    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error configurando particion de boot: %s", esp_err_to_name(err));
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "OTA Exitosa (%d bytes). Reiniciando...", total_read);
    esp_restart();
    return ESP_OK;
}

esp_err_t check_and_update_firmware(const char *json_url, int current_version)
{
    ESP_LOGI(TAG, "Verificando actualizaciones en: %s", json_url);

    esp_http_client_config_t config = {
        .url = json_url,
        .timeout_ms = 5000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);

    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error conectando al servidor de versiones: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    int content_length = esp_http_client_fetch_headers(client);
    if (content_length <= 0) {
        ESP_LOGE(TAG, "El servidor envio una respuesta vacia");
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    char *buffer = malloc(JSON_BUFFER_SIZE);
    int read_len = esp_http_client_read(client, buffer, JSON_BUFFER_SIZE - 1);
    if (read_len <= 0) {
        ESP_LOGE(TAG, "Error leyendo JSON de version");
        free(buffer);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }
    buffer[read_len] = '\0';
    
    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    ESP_LOGI(TAG, "JSON recibido: %s", buffer);
    cJSON *json = cJSON_Parse(buffer);
    if (json == NULL) {
        ESP_LOGE(TAG, "El JSON no es valido");
        free(buffer);
        return ESP_FAIL;
    }

    cJSON *ver_item = cJSON_GetObjectItem(json, "version");
    cJSON *url_item = cJSON_GetObjectItem(json, "url");

    if (!cJSON_IsNumber(ver_item) || !cJSON_IsString(url_item)) {
        ESP_LOGE(TAG, "Formato JSON incorrecto (falta 'version' o 'url')");
        cJSON_Delete(json);
        free(buffer);
        return ESP_FAIL;
    }

    int new_version = ver_item->valueint;
    char *new_url = strdup(url_item->valuestring);

    cJSON_Delete(json);
    free(buffer);

    ESP_LOGI(TAG, "Version Actual: %d | Version Nueva: %d", current_version, new_version);

    if (new_version > current_version) {
        ESP_LOGW(TAG, "Nueva version detectada! Iniciando actualizacion...");
        esp_err_t ret = _run_ota_download(new_url);
        free(new_url);
        return ret;
    } else {
        ESP_LOGI(TAG, "El sistema esta actualizado.");
        free(new_url);
        return ESP_OK;
    }
}
