#include "app_controller.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "driver/i2c.h"
#include "esp_task_wdt.h"
#include "as7265x.h"
#include "nextion.h"
#include "mqtt_app.h"
#include "ota_update.h"

static const char *TAG = "APP_CTRL";

#define EVENT_QUEUE_SIZE        10
#define TIEMPO_ENTRE_MUESTRAS   3000
#define TIEMPO_DEEP_SLEEP_MIN   1
#define I2C_MASTER_NUM          I2C_NUM_0

#define SENSOR_POLL_DELAY_MS    10   
#define SENSOR_TIMEOUT_MS       1000 
#define LED_DRV_CURRENT         0

#define OTA_JSON_URL        "https://raw.githubusercontent.com/LManuXx/Waterly/main/waterly/version.json"
#define CURRENT_FIRMWARE_VER 1

typedef enum {
    STATE_IDLE,
    STATE_TRAINING,
    STATE_SLEEPING,
    STATE_UPDATING,
    STATE_SINGLE_MEASURE
} app_state_t;

static QueueHandle_t event_queue = NULL;
static app_state_t current_state = STATE_IDLE;
static as7265x_handle_t sensor;
static bool sensor_ok = false;

static void iniciar_sensor_interno() {
    ESP_LOGI(TAG, "Buscando sensor AS7265x...");
    
    if (as7265x_init(&sensor, I2C_MASTER_NUM) == ESP_OK) {
        as7265x_set_integration_time(&sensor, 50);
        as7265x_set_bulb_current(&sensor, 0, false);
        sensor_ok = true;
        ESP_LOGI(TAG, "Sensor encontrado y configurado.");
    } else {
        ESP_LOGE(TAG, "FALLO: Sensor no responde.");
        nextion_send_txt("values", "Error Sensor");
        sensor_ok = false;
    }
}

static void ejecutar_ota() {
    ESP_LOGW(TAG, ">>> INICIANDO PROTOCOLO OTA <<<");
    
    nextion_send_txt("values", "SYSTEM UPDATE\rConnecting...");
    
    if (sensor_ok) {
        as7265x_set_bulb_current(&sensor, 0, false);
    }
    
    esp_err_t ret = check_and_update_firmware(OTA_JSON_URL, CURRENT_FIRMWARE_VER);

    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "No había actualización o chequeo finalizado.");
        nextion_send_txt("values", "No Updates");
    } else {
        ESP_LOGE(TAG, "Error en el proceso OTA");
        nextion_send_txt("values", "Update Failed!");
    }
    
    vTaskDelay(pdMS_TO_TICKS(3000));
    esp_restart();
}

static void tomar_medida_y_enviar() {
    if (!sensor_ok) {
        ESP_LOGW(TAG, "Saltando medida (Sensor Offline)");
        nextion_send_txt("values", "Sensor Offline");
        return;
    }

    as7265x_values_t data;
    
    ESP_LOGD(TAG, "Iniciando secuencia de medida...");
    
    as7265x_set_bulb_current(&sensor, LED_DRV_CURRENT, true);
    vTaskDelay(pdMS_TO_TICKS(50)); 

    as7265x_set_config(&sensor, AS7265X_MEASUREMENT_MODE_6_CHAN_ONE_SHOT, AS7265X_GAIN_64X);

    bool data_ready = false;
    int intentos_max = SENSOR_TIMEOUT_MS / SENSOR_POLL_DELAY_MS;
    int intentos = 0;

    while (intentos < intentos_max) {
        if (as7265x_data_ready(&sensor)) {
            data_ready = true;
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(SENSOR_POLL_DELAY_MS));
        intentos++;
    }

    as7265x_set_bulb_current(&sensor, LED_DRV_CURRENT, false);

    if (data_ready) {
        if (as7265x_get_all_values(&sensor, &data) == ESP_OK) {
            
            ESP_LOGI(TAG, "DATA FULL SPECTRUM LEIDA");

            // --- FORMATO LARGO (UV, VIS, NIR) ---
            // Aumentamos el buffer porque el texto es muy largo
            char buffer[256]; 
            
            // Construimos el string con saltos de línea (\r) para que salga ordenado
            snprintf(buffer, sizeof(buffer), 
                "[UV] A:%.2f | B:%.2f | C:%.2f | D:%.2f | E:%.2f | F:%.2f\r"
                "[VIS] G:%.2f | H:%.2f | I:%.2f | J:%.2f | K:%.2f | L:%.2f\r"
                "[NIR] R:%.2f | S:%.2f | T:%.2f | U:%.2f | V:%.2f | W:%.2f",
                data.A, data.B, data.C, data.D, data.E, data.F,
                data.G, data.H, data.I, data.J, data.K, data.L,
                data.R, data.S, data.T, data.U, data.V, data.W);
            
            nextion_send_txt("values", buffer);

            mqtt_app_send_full_spectrum(&data);

        } else {
            ESP_LOGE(TAG, "Error I2C al leer registros");
            nextion_send_txt("values", "Err: I2C Read");
        }
    } else {
        ESP_LOGE(TAG, "Timeout: El sensor nunca terminó de medir");
        nextion_send_txt("values", "Err: Timeout");
    }
}

static void ir_a_dormir() {
    ESP_LOGW(TAG, "Ejecutando secuencia de Deep Sleep...");
    
    nextion_send_txt("values", "Estado: SLEEP\rZzz...");
    
    if (sensor_ok) {
        as7265x_set_bulb_current(&sensor, 0, false);
    }
    
    vTaskDelay(pdMS_TO_TICKS(1000));
    
    nextion_send_cmd("sleep=1");
    
    ESP_LOGI(TAG, "Hasta dentro de %d minutos.", TIEMPO_DEEP_SLEEP_MIN);
    esp_deep_sleep(TIEMPO_DEEP_SLEEP_MIN * 60 * 1000000ULL);
}

static void app_controller_task(void *pvParameters) {
    app_event_t event;
    TickType_t last_wake_time = xTaskGetTickCount();
    esp_task_wdt_add(NULL);
    
    iniciar_sensor_interno();

    // Actualizamos el estado inicial al arrancar
    nextion_send_txt("t0", "Status: IDLE");

    while (1) {
        esp_task_wdt_reset();
        if (xQueueReceive(event_queue, &event, 0) == pdTRUE) {
            
            // --- ACTUALIZACIÓN DE ESTADO EN PANTALLA (t0) ---
            switch (event) {
                case APP_EVENT_GO_IDLE:
                    ESP_LOGI(TAG, ">>> MODO: IDLE <<<");
                    current_state = STATE_IDLE;
                    nextion_send_txt("t0", "Status: IDLE");
                    break;

                case APP_EVENT_START_TRAINING:
                    ESP_LOGI(TAG, ">>> MODO: TRAINING <<<");
                    current_state = STATE_TRAINING;
                    last_wake_time = xTaskGetTickCount();
                    nextion_send_txt("t0", "Status: TRAINING");
                    nextion_send_txt("values", "Training Loop...");
                    break;
                    
                case APP_EVENT_SINGLE_MEASURE:
                    ESP_LOGI(TAG, ">>> MODO: SINGLE MEASURE <<<");
                    current_state = STATE_SINGLE_MEASURE;
                    nextion_send_txt("t0", "Status: MEASURING");
                    nextion_send_txt("values", "Midiendo...");
                    break;

                case APP_EVENT_STOP_AND_SLEEP:
                    ESP_LOGI(TAG, ">>> MODO: SLEEPING <<<");
                    current_state = STATE_SLEEPING;
                    nextion_send_txt("t0", "Status: SLEEP");
                    break;
                
                case APP_EVENT_START_OTA:
                    ESP_LOGW(TAG, ">>> MODO: OTA UPDATE <<<");
                    current_state = STATE_UPDATING;
                    nextion_send_txt("t0", "Status: OTA UPDATE");
                    break;
            }
        }

        switch (current_state) {
            case STATE_IDLE:
                vTaskDelay(pdMS_TO_TICKS(100));
                break;

            case STATE_SINGLE_MEASURE:
                tomar_medida_y_enviar();
                ESP_LOGI(TAG, "Medida única completada, volviendo a IDLE.");
                
                // Volvemos a IDLE automáticamente
                current_state = STATE_IDLE; 
                nextion_send_txt("t0", "Status: IDLE"); // Actualizamos texto al volver
                break;

            case STATE_TRAINING:
                tomar_medida_y_enviar();
                vTaskDelayUntil(&last_wake_time, pdMS_TO_TICKS(TIEMPO_ENTRE_MUESTRAS));
                break;

            case STATE_SLEEPING:
                ir_a_dormir(); 
                break;

            case STATE_UPDATING:
                ejecutar_ota(); 
                break;
        }
    }
}

esp_err_t app_controller_init(void) {
    event_queue = xQueueCreate(EVENT_QUEUE_SIZE, sizeof(app_event_t));
    if (event_queue == NULL) return ESP_FAIL;

    BaseType_t res = xTaskCreate(app_controller_task, "AppCtrl", 4096, NULL, 5, NULL);
    return (res == pdPASS) ? ESP_OK : ESP_FAIL;
}

bool app_controller_send_event(app_event_t event) {
    if (event_queue == NULL) return false;
    if (xQueueSend(event_queue, &event, 0) == pdTRUE) {
        return true;
    }
    return false;
}