#include "app_controller.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "driver/i2c.h"
#include "esp_task_wdt.h"
#include "as7265x.h"
#include "ssd1306.h"
#include "mqtt_app.h"
#include "ota_update.h"


static const char *TAG = "APP_CTRL";

#define EVENT_QUEUE_SIZE        10
#define TIEMPO_ENTRE_MUESTRAS   3000 // ms
#define TIEMPO_DEEP_SLEEP_MIN   1
#define I2C_MASTER_NUM          I2C_NUM_0

#define SENSOR_POLL_DELAY_MS    10   
#define SENSOR_TIMEOUT_MS       1000 
#define LED_DRV_CURRENT         0

#define OTA_JSON_URL        "https://raw.githubusercontent.com/LManuXx/Waterly/main/waterly/version.json"
// Incrementa este número cada vez que subas un código nuevo a GitHub
#define CURRENT_FIRMWARE_VER 1

// --- ESTADOS INTERNOS ---
typedef enum {
    STATE_IDLE,
    STATE_TRAINING,
    STATE_SLEEPING,
    STATE_UPDATING,
    STATE_SINGLE_MEASURE
} app_state_t;

// Variables Privadas
static QueueHandle_t event_queue = NULL;
static app_state_t current_state = STATE_IDLE;
static as7265x_handle_t sensor;
static bool sensor_ok = false;

// --- FUNCIONES PRIVADAS ---

static void iniciar_sensor_interno() {
    ESP_LOGI(TAG, "Buscando sensor AS7265x...");
    
    // NO llamamos a i2c_driver_install, ya lo hizo el Main.
    // Solo inicializamos la estructura del sensor.
    if (as7265x_init(&sensor, I2C_MASTER_NUM) == ESP_OK) {
        // Configuramos parámetros básicos
        as7265x_set_integration_time(&sensor, 50);
        as7265x_set_bulb_current(&sensor, 0, false);
        sensor_ok = true;
        ESP_LOGI(TAG, "Sensor encontrado y configurado.");
    } else {
        ESP_LOGE(TAG, "FALLO: Sensor no responde.");
        ssd1306_print(0, 0, "Error Sensor");
        sensor_ok = false;
    }
}

static void ejecutar_ota() {
    ESP_LOGW(TAG, ">>> INICIANDO PROTOCOLO OTA <<<");
    
    // 1. Feedback Visual
    ssd1306_clear();
    ssd1306_print(0, 0, "SYSTEM UPDATE");
    ssd1306_print(2, 0, "Connecting...");
    
    // 2. Apagar Hardware Peligroso
    // Es vital apagar LEDs y sensores para liberar corriente y evitar estados inestables
    if (sensor_ok) {
        as7265x_set_bulb_current(&sensor, 0, false);
    }
    
    // 3. Ejecutar la lógica de tu archivo ota_update.c
    // Esta función bloqueará la ejecución hasta que termine o falle.
    // Si tiene éxito, el ESP32 se reiniciará DENTRO de esa función.
    esp_err_t ret = check_and_update_firmware(OTA_JSON_URL, CURRENT_FIRMWARE_VER);

    // 4. Gestión de Errores
    // Si llegamos aquí, es que NO se reinició (falló o no había update)
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "No había actualización o chequeo finalizado.");
        ssd1306_print(2, 0, "No Updates");
    } else {
        ESP_LOGE(TAG, "Error en el proceso OTA");
        ssd1306_print(2, 0, "Update Failed!");
    }
    
    // Esperamos un poco y reiniciamos para limpiar el sistema
    vTaskDelay(pdMS_TO_TICKS(3000));
    esp_restart();
}

static void tomar_medida_y_enviar() {
    // 1. Verificación de Seguridad
    if (!sensor_ok) {
        ESP_LOGW(TAG, "Saltando medida (Sensor Offline)");
        return;
    }

    as7265x_values_t data;
    
    // --- PASO A: PREPARACIÓN ---
    ESP_LOGD(TAG, "Iniciando secuencia de medida...");
    
    // Encendemos el LED blanco (Target) para iluminar la muestra
    // true = encender, false = apagar. 
    // Usamos el driver current que definas (ej. 12.5mA)
    as7265x_set_bulb_current(&sensor, LED_DRV_CURRENT, true);

    // Pequeña espera para estabilizar la luz (opcional, 50ms suele bastar)
    vTaskDelay(pdMS_TO_TICKS(50)); 

    // --- PASO B: DISPARO (TRIGGER) ---
    // Configuración "One Shot": Mide una vez, guarda y espera.
    // Gain 64x es bueno para interiores/laboratorio.
    as7265x_set_config(&sensor, AS7265X_MEASUREMENT_MODE_6_CHAN_ONE_SHOT, AS7265X_GAIN_64X);

    // --- PASO C: SONDEO INTELIGENTE (POLLING) ---
    bool data_ready = false;
    int intentos_max = SENSOR_TIMEOUT_MS / SENSOR_POLL_DELAY_MS;
    int intentos = 0;

    while (intentos < intentos_max) {
        // Preguntamos al chip: ¿Tienes datos?
        if (as7265x_data_ready(&sensor)) {
            data_ready = true;
            break; // ¡Ya los tiene! Salimos del bucle
        }
        // Si no, esperamos un poco y volvemos a preguntar
        vTaskDelay(pdMS_TO_TICKS(SENSOR_POLL_DELAY_MS));
        intentos++;
    }

    // --- PASO D: APAGADO INMEDIATO ---
    // Apagamos la luz YA para ahorrar batería y no calentar el chip
    as7265x_set_bulb_current(&sensor, LED_DRV_CURRENT, false);

    // --- PASO E: LECTURA Y VISUALIZACIÓN ---
    if (data_ready) {
        // Leer los registros del chip
        if (as7265x_get_all_values(&sensor, &data) == ESP_OK) {
            
            // Log para debug (Consola)
            ESP_LOGI(TAG, "DATA -> UV(A): %.1f | VIS(G): %.1f | NIR(W): %.1f", 
                     data.A, data.G, data.W);

            // 1. ACTUALIZAR PANTALLA OLED (Formato limpio)
            char buffer[20]; // Buffer temporal para textos
            
            ssd1306_clear(); // Limpiamos pantalla
            ssd1306_print(0, 0, "--- WATERLY ---"); // Cabecera
            
            // Línea UV (Canal A aprox 410nm)
            snprintf(buffer, sizeof(buffer), "UV : %6.1f", data.A);
            ssd1306_print(2, 0, buffer);

            // Línea VISIBLE (Canal G aprox 560nm - Verde/Amarillo)
            snprintf(buffer, sizeof(buffer), "VIS: %6.1f", data.G);
            ssd1306_print(3, 0, buffer);

            // Línea INFRARROJO (Canal W aprox 860nm)
            snprintf(buffer, sizeof(buffer), "NIR: %6.1f", data.W);
            ssd1306_print(4, 0, buffer);

            // Estado (Pie de página)
            ssd1306_print(7, 0, "Estado: OK  MQTT>>");

            // 2. ENVIAR A MQTT (Nube)
            // Enviamos con precisión decimal si cambiaste la función mqtt a float, 
            // si no, el cast (int) está bien para pruebas.
            mqtt_app_send_data((int)data.A, (int)data.G, (int)data.W);

        } else {
            ESP_LOGE(TAG, "Error I2C al leer registros");
            ssd1306_print(7, 0, "Err: I2C Read");
        }
    } else {
        ESP_LOGE(TAG, "Timeout: El sensor nunca terminó de medir");
        ssd1306_print(7, 0, "Err: Timeout");
    }
}

static void ir_a_dormir() {
    ESP_LOGW(TAG, "Ejecutando secuencia de Deep Sleep...");
    ssd1306_print(4, 0, "Estado: SLEEP");
    ssd1306_print(5, 0, "Zzz...");
    
    // Apagamos todo lo apagable
    if (sensor_ok) {
        as7265x_set_bulb_current(&sensor, 0, false);
    }
    
    // Esperamos un segundo para que salgan los logs y mensajes MQTT pendientes
    vTaskDelay(pdMS_TO_TICKS(1000));
    
    // Apagamos pantalla
    ssd1306_clear();
    
    ESP_LOGI(TAG, "Hasta dentro de %d minutos.", TIEMPO_DEEP_SLEEP_MIN);
    esp_deep_sleep(TIEMPO_DEEP_SLEEP_MIN * 60 * 1000000ULL);
}

// --- TAREA PRINCIPAL (CEREBRO) ---
static void app_controller_task(void *pvParameters) {
    app_event_t event;
    TickType_t last_wake_time = xTaskGetTickCount();
    esp_task_wdt_add(NULL);
    // 1. Inicializar Hardware local (Sensor)
    iniciar_sensor_interno();

    while (1) {
        esp_task_wdt_reset();
        if (xQueueReceive(event_queue, &event, 0) == pdTRUE) {
            switch (event) {
                case APP_EVENT_GO_IDLE:
                    ESP_LOGI(TAG, ">>> MODO: IDLE <<<");
                    current_state = STATE_IDLE;
                    ssd1306_clear();
                    ssd1306_print(0, 0, "=== WATERLY ===");
                    ssd1306_print(3, 0, "  Esperando...");
                    ssd1306_print(7, 0, "Modo: IDLE  :) ");
                    break;

                case APP_EVENT_START_TRAINING:
                    ESP_LOGI(TAG, ">>> MODO: TRAINING <<<");
                    current_state = STATE_TRAINING;
                    break;
                    
                case APP_EVENT_SINGLE_MEASURE:
                    ESP_LOGI(TAG, ">>> MODO: SINGLE MEASURE <<<");
                    current_state = STATE_SINGLE_MEASURE;
                    break;

                case APP_EVENT_STOP_AND_SLEEP:
                    ESP_LOGI(TAG, ">>> MODO: SLEEPING <<<");
                    current_state = STATE_SLEEPING;
                    break;
                
                case APP_EVENT_START_OTA:
                    ESP_LOGW(TAG, ">>> MODO: OTA UPDATE <<<");
                    current_state = STATE_UPDATING;
                    break;
            }
        }

        // B. MÁQUINA DE ESTADOS
        switch (current_state) {
            case STATE_IDLE:
                // No hacemos nada intensivo, solo esperamos el siguiente evento.
                vTaskDelay(pdMS_TO_TICKS(100));
                break;

            case STATE_SINGLE_MEASURE:
                ssd1306_print(7, 0, "Midiendo...  ");
                tomar_medida_y_enviar(); // Usamos la misma función de medida
                
                // TRUCO: Volvemos a IDLE automáticamente tras medir una vez
                ESP_LOGI(TAG, "Medida única completada, volviendo a IDLE.");
                current_state = STATE_IDLE; 
                ssd1306_print(7, 0, "Modo: IDLE   ");
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

// --- IMPLEMENTACIÓN PÚBLICA ---

esp_err_t app_controller_init(void) {
    event_queue = xQueueCreate(EVENT_QUEUE_SIZE, sizeof(app_event_t));
    if (event_queue == NULL) return ESP_FAIL;

    // Creamos la tarea. IMPORTANTE: Stack suficiente (4096)
    BaseType_t res = xTaskCreate(app_controller_task, "AppCtrl", 4096, NULL, 5, NULL);
    return (res == pdPASS) ? ESP_OK : ESP_FAIL;
}

bool app_controller_send_event(app_event_t event) {
    if (event_queue == NULL) return false;
    if (xQueueSend(event_queue, &event, 0) == pdTRUE) {
        return true;
    }
    return false; // Cola llena
}