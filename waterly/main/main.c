#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "wifi_connect.h"
#include "as7265x.h"

// ---------------------------------------------------------
// Configuraciones Hardware
// ---------------------------------------------------------
#define I2C_MASTER_SCL_IO           22
#define I2C_MASTER_SDA_IO           21
#define I2C_MASTER_NUM              I2C_NUM_0
#define I2C_MASTER_FREQ_HZ          100000 

#define LED_CURRENT_12MA  0
#define LED_CURRENT_25MA  1
#define LED_CURRENT_50MA  2
#define LED_CURRENT_100MA 3

static const char *TAG = "MAIN";

/**
 * @brief Tarea principal de medición del sensor.
 * Se ejecuta en paralelo una vez que hay WiFi.
 */
void sensor_task(void *pvParameters)
{
    // ---------------------------------------------------------
    // 1. Configuración I2C
    // ---------------------------------------------------------
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
    i2c_driver_install(I2C_MASTER_NUM, conf.mode, 0, 0, 0);

    // ---------------------------------------------------------
    // 2. Inicializar Sensor
    // ---------------------------------------------------------
    as7265x_handle_t sensor;
    ESP_LOGI("SENSOR", "Iniciando hardware espectral...");
    vTaskDelay(pdMS_TO_TICKS(1000)); // Espera de estabilización

    if (as7265x_init(&sensor, I2C_MASTER_NUM) != ESP_OK) {
        ESP_LOGE("SENSOR", "Fallo al iniciar AS7265x. Tarea detenida.");
        vTaskDelete(NULL); // Matamos la tarea si no hay sensor
    }
    
    // Configuración base
    as7265x_set_integration_time(&sensor, 50); // ~140ms
    as7265x_gain_t ganancia = AS7265X_GAIN_64X;
    as7265x_set_bulb_current(&sensor, LED_CURRENT_12MA, false);

    as7265x_values_t data;

    // ---------------------------------------------------------
    // 3. Bucle de Medición
    // ---------------------------------------------------------
    while (1) {
        ESP_LOGI("SENSOR", "--- Iniciando Captura (One-Shot) ---");

        // A) Encender Luz
        as7265x_set_bulb_current(&sensor, LED_CURRENT_12MA, true);
        vTaskDelay(pdMS_TO_TICKS(100)); 

        // B) Disparar medición
        as7265x_set_config(&sensor, AS7265X_MEASUREMENT_MODE_6_CHAN_ONE_SHOT, ganancia);

        // C) Esperar datos (Polling)
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

        // E) Leer y Procesar
        if (ready) {
            if (as7265x_get_all_values(&sensor, &data) == ESP_OK) {
                ESP_LOGI("SENSOR", "Lectura Exitosa:");
                ESP_LOGI("SENSOR", "  UV (A-410nm):  %.2f", data.A);
                ESP_LOGI("SENSOR", "  Vis (G-560nm): %.2f", data.G);
                ESP_LOGI("SENSOR", "  NIR (W-860nm): %.2f", data.W);
                
                // TODO: Aquí pondrás tu llamada a la API REST o MQTT
                // send_data_to_cloud(data); 
                
            } else {
                ESP_LOGE("SENSOR", "Error leyendo datos I2C");
            }
        } else {
            ESP_LOGW("SENSOR", "Timeout esperando datos");
        }

        // Esperar 5 segundos antes de la siguiente muestra
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
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

    if (wifi_connect_init() == ESP_OK) {
        
        ESP_LOGI(TAG, "WiFi Conectado. Lanzando tarea de sensores...");
        
        xTaskCreate(sensor_task, "sensor_task", 4096, NULL, 5, NULL);

    } else {
        ESP_LOGE(TAG, "No se pudo conectar al WiFi. Reiniciando...");
        vTaskDelay(pdMS_TO_TICKS(5000));
        esp_restart();
    }
}