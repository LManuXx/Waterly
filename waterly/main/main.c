#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c.h"
#include "esp_log.h"
#include "../components/as7265x/include/as7265x.h"

#define I2C_MASTER_SCL_IO           22
#define I2C_MASTER_SDA_IO           21
#define I2C_MASTER_NUM              I2C_NUM_0
// Frecuencia conservadora para evitar errores en el puente virtual [cite: 541]
#define I2C_MASTER_FREQ_HZ          100000 

static const char *TAG = "MAIN";

// Códigos de corriente para el LED (según Datasheet [cite: 724])
#define LED_CURRENT_12MA  0
#define LED_CURRENT_25MA  1
#define LED_CURRENT_50MA  2
#define LED_CURRENT_100MA 3

void app_main(void)
{
    // ---------------------------------------------------------
    // 1. Configuración I2C
    // ---------------------------------------------------------
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .scl_io_num = I2C_MASTER_SCL_IO,
        // Habilitamos pull-ups internos. 
        // IMPORTANTE: Si tienes resistencias físicas de 4.7k, cambia esto a DISABLE.
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
    ESP_LOGI(TAG, "Inicializando AS7265x...");
    // Espera inicial para que el sensor arranque su firmware interno
    vTaskDelay(pdMS_TO_TICKS(1000)); 

    if (as7265x_init(&sensor, I2C_MASTER_NUM) != ESP_OK) {
        ESP_LOGE(TAG, "FALLO CRITICO: Sensor no responde. Revisa cableado.");
        return;
    }
    ESP_LOGI(TAG, "Sensor AS7265x detectado y listo.");

    // ---------------------------------------------------------
    // 3. Configuración Inicial del Sensor
    // ---------------------------------------------------------
    
    // Configuración de Tiempo de Integración [cite: 711]
    // Valor 50 * 2.8ms = 140ms por ciclo de integración.
    uint8_t int_cycles = 50; 
    as7265x_set_integration_time(&sensor, int_cycles);

    // Ganancia [cite: 703]
    // Usamos 16X para evitar saturación (valores máximos) en las primeras pruebas.
    // Si sale muy oscuro, sube a 64X.
    as7265x_gain_t ganancia = AS7265X_GAIN_64X;

    // Aseguramos que el LED empiece apagado [cite: 724]
    as7265x_set_bulb_current(&sensor, LED_CURRENT_12MA, false);

    as7265x_values_t data;

    // Bucle Principal
    while (1) {
        ESP_LOGI(TAG, "--- Iniciando Captura Sincronizada (One-Shot) ---");

        // PASO A: Encender la iluminación
        // Esto actúa como el obturador electrónico [cite: 513]
        as7265x_set_bulb_current(&sensor, LED_CURRENT_12MA, true);
        
        // Espera breve para que el filamento/LED se estabilice
        vTaskDelay(pdMS_TO_TICKS(100)); 

        // PASO B: Disparar la medición (TRIGGER)
        // Configuramos en Modo 3 (One-Shot). Esto reinicia el ciclo y captura los 18 canales.
        // Al escribir en este registro, el sensor comienza a integrar inmediatamente. [cite: 703]
        as7265x_set_config(&sensor, AS7265X_MEASUREMENT_MODE_6_CHAN_ONE_SHOT, ganancia);

        // PASO C: Esperar a que el sensor termine (POLLING)
        // El tiempo teórico mínimo es: 140ms * 2 bancos = 280ms [cite: 447, 523]
        // Hacemos un bucle preguntando "¿Estás listo?" para mantener la sincronización perfecta.
        bool data_ready = false;
        int timeout_counter = 0;
        int max_retries = 20; // 20 * 50ms = 1 segundo de timeout máximo

        while (timeout_counter < max_retries) {
            vTaskDelay(pdMS_TO_TICKS(50)); // Preguntar cada 50ms
            
            if (as7265x_data_ready(&sensor)) {
                data_ready = true;
                break; // ¡Datos listos! Salir del bucle de espera
            }
            timeout_counter++;
        }

        // PASO D: Apagar la iluminación INMEDIATAMENTE
        // Ya tenemos los datos en el buffer del sensor, apagamos para no calentar la muestra.
        as7265x_set_bulb_current(&sensor, LED_CURRENT_12MA, false);

        // PASO E: Leer y procesar
        if (data_ready) {
            ESP_LOGI(TAG, "Integración completa. Leyendo registros...");

            // Leemos los registros virtuales [cite: 681]
            if (as7265x_get_all_values(&sensor, &data) == ESP_OK) {
                // Imprimimos los canales clave para tu proyecto (Nitrato/Potasio/Sodio)
                // UV (A), Visible (G, R), NIR (L)
                ESP_LOGI(TAG, "Resultados:");
                ESP_LOGI(TAG, "  Violeta (A - 410nm): %.2f", data.A);
                ESP_LOGI(TAG, "  Verde   (G - 560nm): %.2f", data.G);
                ESP_LOGI(TAG, "  Rojo    (R - 610nm): %.2f", data.R);
                ESP_LOGI(TAG, "  NIR 1   (V - 810nm): %.2f", data.V);
                ESP_LOGI(TAG, "  NIR 2   (L - 940nm): %.2f", data.L);
                
                // Verificación de cordura:
                if (data.R == 0.0f && data.G == 0.0f && data.A == 0.0f) {
                    ESP_LOGW(TAG, "ALERTA: Lectura de ceros. Verifica si el LED encendió.");
                }
            } else {
                ESP_LOGE(TAG, "Error en la transferencia I2C durante la lectura.");
            }
        } else {
            ESP_LOGW(TAG, "TIMEOUT: El sensor no respondió a tiempo (DATA_RDY nunca fue 1).");
        }

        ESP_LOGI(TAG, "--- Fin de ciclo, esperando 3s ---");
        vTaskDelay(pdMS_TO_TICKS(3000));
    }
}