#include "nvs_flash.h"
#include "esp_log.h"
#include "driver/i2c.h" // El driver I2C se gestiona aquí
#include "wifi_connect.h"
#include "ssd1306.h"
#include "esp_task_wdt.h"

#include "mqtt_app.h"
#include "app_controller.h" // <--- El nuevo cerebro

static const char *TAG = "MAIN";

// Definición de pines I2C (Centralizada)
#define I2C_MASTER_SCL_IO           22
#define I2C_MASTER_SDA_IO           21
#define I2C_MASTER_NUM              I2C_NUM_0
#define I2C_MASTER_FREQ_HZ          100000 

void app_main(void)
{
    // 1. INICIALIZACIÓN DEL SISTEMA
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(TAG, "Arrancando Waterly Modular...");

    // 2. INSTALAR I2C (GLOBAL)
    // Se hace aquí una vez para evitar conflictos entre pantalla y sensor
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

    // 3. INICIAR PANTALLA
    ssd1306_init(I2C_MASTER_NUM);
    ssd1306_clear();
    ssd1306_print(0, 0, "System Init...");

    // 4. INICIAR EL CEREBRO (APP CONTROLLER)
    // Esto crea la cola y lanza la tarea FSM en segundo plano
    if (app_controller_init() == ESP_OK) {
        ESP_LOGI(TAG, "App Controller: OK");
    } else {
        ESP_LOGE(TAG, "App Controller: FAIL");
    }

    // 5. CONECTAR WIFI Y MQTT
    if (wifi_connect_init() == ESP_OK) {
        ssd1306_print(2, 0, "WiFi OK");
        
        ESP_LOGI(TAG, "Iniciando MQTT...");
        mqtt_app_start();
        
        // 6. SINCRONIZACIÓN (Esperar órdenes del servidor)
        ssd1306_print(4, 0, "Sync MQTT...");
        ESP_LOGI(TAG, "Esperando 5s para recibir configuración MQTT...");
        
        // Durante este delay, si MQTT recibe algo, enviará un evento al controlador
        vTaskDelay(pdMS_TO_TICKS(5000));

        // 7. DECISIÓN POR DEFECTO
        // Si no ha llegado ninguna orden de "Dormir" (el estado sigue en IDLE),
        // arrancamos el modo entrenamiento por defecto.
        // Nota: app_controller gestiona internamente si cambia de estado.
        ESP_LOGW(TAG, "Enviando señal de arranque por defecto...");
        app_controller_send_event(APP_EVENT_GO_IDLE);

    } else {
        ssd1306_print(2, 0, "Error WiFi");
        ESP_LOGE(TAG, "Fallo crítico WiFi");
    }

    // El main termina aquí, pero la tarea de app_controller sigue viva.
}