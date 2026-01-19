#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_task_wdt.h"

#include <time.h>
#include <sys/time.h>
#include "esp_sntp.h"

#define VERSION_JSON_URL "https://raw.githubusercontent.com/LManuXx/Waterly/main/waterly/version.json"
#define CURRENT_FIRMWARE_VERSION 1

#include "wifi_connect.h"
#include "as7265x.h"
#include "ota_update.h"
#include "ssd1306.h"

// ---------------------------------------------------------
// Configuraciones Hardware
// ---------------------------------------------------------
#define I2C_MASTER_SCL_IO           22
#define I2C_MASTER_SDA_IO           21
#define I2C_MASTER_NUM              I2C_NUM_0
#define I2C_MASTER_FREQ_HZ          100000 

#define LED_CURRENT_12MA  0

static const char *TAG = "MAIN";

void setup_time(void)
{
    ESP_LOGI(TAG, "Inicializando SNTP (Obteniendo hora)...");
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_init();
    setenv("TZ", "CET-1CEST,M3.5.0,M10.5.0/3", 1);
    tzset();

    int retry = 0;
    const int retry_count = 10; // Bajamos reintentos para no bloquear tanto
    while (sntp_get_sync_status() == SNTP_SYNC_STATUS_RESET && ++retry < retry_count) {
        ESP_LOGI(TAG, "Esperando hora... (%d/%d)", retry, retry_count);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }

    if (retry == retry_count) {
        ESP_LOGW(TAG, "No se pudo obtener hora. Usando default.");
    } else {
        time_t now;
        struct tm timeinfo;
        time(&now);
        localtime_r(&now, &timeinfo);
        char strftime_buf[64];
        strftime(strftime_buf, sizeof(strftime_buf), "%c", &timeinfo);
        ESP_LOGI(TAG, "Hora sincronizada: %s", strftime_buf);
    }
}

void sensor_task(void *pvParameters)
{
    as7265x_handle_t sensor;
    ESP_LOGI("SENSOR", "Iniciando hardware espectral...");
    vTaskDelay(pdMS_TO_TICKS(1000)); 

    // Aquí ya NO instalamos driver, solo iniciamos el objeto sensor
    if (as7265x_init(&sensor, I2C_MASTER_NUM) != ESP_OK) {
        ESP_LOGE("SENSOR", "Fallo al iniciar AS7265x. ¿Cableado o Driver duplicado?");
        
        // Mensaje de error en pantalla para que lo veas
        ssd1306_clear();
        ssd1306_print(0, 0, "ERROR SENSOR");
        ssd1306_print(2, 0, "Revisar Cables");
        
        vTaskDelete(NULL); 
    }
    
    as7265x_set_integration_time(&sensor, 50); 
    as7265x_gain_t ganancia = AS7265X_GAIN_64X;
    as7265x_set_bulb_current(&sensor, LED_CURRENT_12MA, false);

    as7265x_values_t data;
    esp_task_wdt_add(NULL); 

    while (1) {
        esp_task_wdt_reset(); 
        ESP_LOGI("SENSOR", "--- Iniciando Captura ---");
        
        // Actualizamos pantalla para saber que está vivo
        ssd1306_print(0, 0, "Midiendo...");

        // A) Encender Luz
        as7265x_set_bulb_current(&sensor, LED_CURRENT_12MA, true);
        vTaskDelay(pdMS_TO_TICKS(100)); 

        // B) Disparar
        as7265x_set_config(&sensor, AS7265X_MEASUREMENT_MODE_6_CHAN_ONE_SHOT, ganancia);

        // C) Polling
        bool ready = false;
        int retries = 0;
        while (retries < 20) {
            vTaskDelay(pdMS_TO_TICKS(50));
            if (as7265x_data_ready(&sensor)) {
                ready = true;
                break;
            }
            retries++;
        }

        // D) Apagar Luz
        as7265x_set_bulb_current(&sensor, LED_CURRENT_12MA, false);

        // E) Leer
        if (ready) {
            if (as7265x_get_all_values(&sensor, &data) == ESP_OK) {
                time_t now;
                struct tm timeinfo;
                time(&now);
                localtime_r(&now, &timeinfo);
                char time_buf[64];
                strftime(time_buf, sizeof(time_buf), "%H:%M:%S", &timeinfo); // Solo hora para pantalla

                ESP_LOGI("SENSOR", "Lectura OK");
                
                // PANTALLA
                char buf[64]; // <--- IMPORTANTE: Tamaño 64
                ssd1306_clear();
                ssd1306_print(0, 0, "Waterly OK");
                
                // CAMBIO AQUÍ: Usamos sizeof(buf) en lugar de 16
                snprintf(buf, sizeof(buf), "UV: %.1f", data.A);
                ssd1306_print(2, 0, buf);

                snprintf(buf, sizeof(buf), "VIS:%.1f", data.G);
                ssd1306_print(3, 0, buf);
                
                snprintf(buf, sizeof(buf), "NIR:%.1f", data.W);
                ssd1306_print(4, 0, buf);
                
                // AQUÍ ES DONDE FALLABA: Ahora con sizeof(buf) ya caben los 64 bytes
                snprintf(buf, sizeof(buf), "%s", time_buf);
                ssd1306_print(7, 0, buf);

            } else {
                ESP_LOGE("SENSOR", "Error I2C");
                ssd1306_print(0, 0, "Error I2C Lectura");
            }
        } else {
            ESP_LOGW("SENSOR", "Timeout");
            ssd1306_print(0, 0, "Timeout Sensor");
        }

        vTaskDelay(pdMS_TO_TICKS(5000));
        esp_task_wdt_reset();
    }
    esp_task_wdt_delete(NULL);
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
      ESP_ERROR_CHECK(nvs_flash_erase());
      ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(TAG, "Arrancando sistema...");

    // 1. Instalar I2C Globalmente
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .scl_io_num = I2C_MASTER_SCL_IO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_MASTER_FREQ_HZ,
        .clk_flags = 0,
    };
    i2c_param_config(I2C_MASTER_NUM, &conf);
    ESP_ERROR_CHECK(i2c_driver_install(I2C_MASTER_NUM, conf.mode, 0, 0, 0));

    // 2. INICIAR PANTALLA (¡DESCOMENTADO E IMPRESCINDIBLE!)
    ssd1306_init(I2C_MASTER_NUM);
    ssd1306_clear();
    ssd1306_print(0, 0, "----------------");
    ssd1306_print(2, 20, "HoLA MANU"); 
    ssd1306_print(4, 30, "FuNCIONA");
    ssd1306_print(7, 0, "----------------");
    

    /*if (wifi_connect_init() == ESP_OK) {
        
        ssd1306_print(2, 0, "WiFi OK!");
        ssd1306_print(3, 0, "Sync Hora...");
        
        ESP_LOGI(TAG, "WiFi Conectado. Sincronizando reloj...");
        setup_time();
        
        ssd1306_print(3, 0, "Hora OK!");
        vTaskDelay(pdMS_TO_TICKS(1000));
        
        ESP_LOGI(TAG, "Lanzando sensores...");
        xTaskCreate(sensor_task, "sensor_task", 4096, NULL, 5, NULL);

    } else {
        ESP_LOGE(TAG, "Fallo WiFi");
        ssd1306_print(2, 0, "Error WiFi");
        vTaskDelay(pdMS_TO_TICKS(5000));
        esp_restart();
    }*/
}