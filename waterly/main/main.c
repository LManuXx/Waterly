#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_task_wdt.h"
#include "esp_sleep.h" // Necesario para Deep Sleep

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
// MODULARIDAD: Componente MQTT
// ---------------------------------------------------------
#include "mqtt_app.h" 

// ---------------------------------------------------------
// Configuraciones Hardware
// ---------------------------------------------------------
#define I2C_MASTER_SCL_IO           22
#define I2C_MASTER_SDA_IO           21
#define I2C_MASTER_NUM              I2C_NUM_0
#define I2C_MASTER_FREQ_HZ          100000 
#define LED_CURRENT_12MA            0

// Configuración de tiempos
#define TIEMPO_ESPERA_MQTT_SEG      5
#define TIEMPO_DEEP_SLEEP_MIN       1  // Tiempo que dormirá en modo ahorro
#define TIEMPO_ENTRE_MUESTRAS_MS    500 // Velocidad en modo entrenamiento (rápido)

static const char *TAG = "MAIN";

// --- Funciones Auxiliares ---

void setup_time(void)
{
    ESP_LOGI(TAG, "Inicializando SNTP...");
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_init();
    setenv("TZ", "CET-1CEST,M3.5.0,M10.5.0/3", 1);
    tzset();

    int retry = 0;
    const int retry_count = 5;
    while (sntp_get_sync_status() == SNTP_SYNC_STATUS_RESET && ++retry < retry_count) {
        ESP_LOGI(TAG, "Esperando hora... (%d/%d)", retry, retry_count);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

// Inicializa el sensor AS7265x
bool iniciar_sensor(as7265x_handle_t *sensor) {
    ESP_LOGI("SENSOR", "Iniciando hardware espectral...");
    if (as7265x_init(sensor, I2C_MASTER_NUM) != ESP_OK) {
        ESP_LOGE("SENSOR", "Fallo al iniciar AS7265x.");
        ssd1306_clear();
        ssd1306_print(0, 0, "ERROR SENSOR");
        return false;
    }
    
    as7265x_set_integration_time(sensor, 50); 
    as7265x_set_bulb_current(sensor, LED_CURRENT_12MA, false);
    return true;
}

// Función CORE: Realiza UN ciclo de medición y envío
// Retorna true si todo fue bien
bool tomar_medida_y_enviar(as7265x_handle_t *sensor) {
    as7265x_values_t data;
    as7265x_gain_t ganancia = AS7265X_GAIN_64X;

    ESP_LOGI("SENSOR", "--- Iniciando Captura ---");
    ssd1306_print(0, 0, "Midiendo...");

    // A) Encender Luz
    as7265x_set_bulb_current(sensor, LED_CURRENT_12MA, true);
    vTaskDelay(pdMS_TO_TICKS(100)); 

    // B) Disparar
    as7265x_set_config(sensor, AS7265X_MEASUREMENT_MODE_6_CHAN_ONE_SHOT, ganancia);

    // C) Polling (Esperar dato)
    bool ready = false;
    int retries = 0;
    while (retries < 20) {
        vTaskDelay(pdMS_TO_TICKS(50));
        if (as7265x_data_ready(sensor)) {
            ready = true;
            break;
        }
        retries++;
    }

    // D) Apagar Luz
    as7265x_set_bulb_current(sensor, LED_CURRENT_12MA, false);

    // E) Procesar
    if (ready) {
        if (as7265x_get_all_values(sensor, &data) == ESP_OK) {
            // Hora
            time_t now;
            struct tm timeinfo;
            time(&now);
            localtime_r(&now, &timeinfo);
            char time_buf[64];
            strftime(time_buf, sizeof(time_buf), "%H:%M:%S", &timeinfo);

            ESP_LOGI("SENSOR", "Lectura OK -> Enviando...");

            // 1. PANTALLA
            char buf[64];
            ssd1306_clear();
            ssd1306_print(0, 0, "Waterly OK");
            
            snprintf(buf, sizeof(buf), "UV: %.1f", data.A);
            ssd1306_print(2, 0, buf);
            snprintf(buf, sizeof(buf), "VIS:%.1f", data.G);
            ssd1306_print(3, 0, buf);
            snprintf(buf, sizeof(buf), "NIR:%.1f", data.W);
            ssd1306_print(4, 0, buf);
            snprintf(buf, sizeof(buf), "%s", time_buf);
            ssd1306_print(7, 0, buf);

            // 2. MQTT (Enviar a Docker)
            // Convertimos a int para simplificar, puedes cambiarlo si quieres float
            mqtt_app_send_data((int)data.A, (int)data.G, (int)data.W);
            
            return true;
        }
    }
    
    ESP_LOGE("SENSOR", "Fallo en lectura");
    ssd1306_print(0, 0, "Error Lectura");
    return false;
}

// Tarea para Modo Entrenamiento (Bucle Infinito)
// Tarea para Modo Entrenamiento
void task_modo_entrenamiento(void *pvParameters) {
    as7265x_handle_t sensor;
    
    if (!iniciar_sensor(&sensor)) {
        vTaskDelete(NULL);
    }

    esp_task_wdt_add(NULL);

    while(1) {
        esp_task_wdt_reset();

        // Revisamos si ha llegado una orden de parar (Deep Sleep) por MQTT
        if (modo_entrenamiento_activo == false) {
            ESP_LOGW(TAG, "Detectada orden de PARADA. Entrando en Deep Sleep...");
            ssd1306_print(4, 0, "Orden: SLEEP");
            
            // Damos un momento para asegurar que el log salga
            vTaskDelay(pdMS_TO_TICKS(1000));
            
            // Apagamos luz del sensor por seguridad antes de dormir
            as7265x_set_bulb_current(&sensor, LED_CURRENT_12MA, false);
            
            // Nos vamos a dormir el tiempo estipulado
            esp_deep_sleep(TIEMPO_DEEP_SLEEP_MIN * 60 * 1000000ULL);
        }
        // --------------------------------------

        tomar_medida_y_enviar(&sensor);
        
        // En modo entrenamiento vamos rápido
        vTaskDelay(pdMS_TO_TICKS(TIEMPO_ENTRE_MUESTRAS_MS));
    }
}

// --- MAIN PRINCIPAL ---

void app_main(void)
{
    // 1. Inicialización Sistema y NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
      ESP_ERROR_CHECK(nvs_flash_erase());
      ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(TAG, "Arrancando sistema...");

    // 2. Instalar I2C
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

    // 3. Iniciar Pantalla
    ssd1306_init(I2C_MASTER_NUM);
    ssd1306_clear();
    ssd1306_print(0, 0, "Waterly Boot...");

    // 4. Conectar WiFi
    if (wifi_connect_init() == ESP_OK) {
        
        ssd1306_print(2, 0, "WiFi OK");
        setup_time(); // Hora

        // 5. Iniciar MQTT
        ESP_LOGI(TAG, "Iniciando MQTT...");
        mqtt_app_start();
        
        ssd1306_print(4, 0, "Check Config...");
        ESP_LOGI(TAG, "Esperando %d s para recibir comandos MQTT...", TIEMPO_ESPERA_MQTT_SEG);
        
        // Esperamos X segundos para dar tiempo a recibir mensajes retenidos
        vTaskDelay(pdMS_TO_TICKS(TIEMPO_ESPERA_MQTT_SEG * 1000));

        // Leemos la variable global definida en mqtt_app
        if (modo_entrenamiento_activo) {
            // >>> MODO ENTRENAMIENTO (NO DUERME) <<<
            ESP_LOGW(TAG, "MODO: ENTRENAMIENTO ACTIVO");
            ssd1306_print(4, 0, "Modo: TRAINING");
            vTaskDelay(pdMS_TO_TICKS(1000));
            
            // Creamos la tarea que nunca termina
            xTaskCreate(task_modo_entrenamiento, "training_task", 4096, NULL, 5, NULL);

        } else {
            // >>> MODO AHORRO (DEEP SLEEP) <<<
            ESP_LOGI(TAG, "MODO: AHORRO DE ENERGIA");
            ssd1306_print(4, 0, "Modo: SLEEP");
            
            // Inicializamos sensor localmente (porque no usamos tarea)
            as7265x_handle_t sensor_local;
            if (iniciar_sensor(&sensor_local)) {
                // Medimos UNA vez
                tomar_medida_y_enviar(&sensor_local);
            }
            
            ESP_LOGI(TAG, "Durmiendo %d minutos...", TIEMPO_DEEP_SLEEP_MIN);
            ssd1306_print(6, 0, "Durmiendo...");
            
            // Damos tiempo a que el mensaje MQTT salga de la cola de envío
            vTaskDelay(pdMS_TO_TICKS(2000));
            
            // Apagamos pantalla para ahorrar
            ssd1306_clear();
            
            // Bye bye
            esp_deep_sleep(TIEMPO_DEEP_SLEEP_MIN * 60 * 1000000ULL);
        }

    } else {
        ESP_LOGE(TAG, "Fallo WiFi crítico");
        ssd1306_print(2, 0, "Error WiFi");
        vTaskDelay(pdMS_TO_TICKS(5000));
        esp_restart();
    }
}