#include "nvs_flash.h"
#include "esp_log.h"
#include "driver/i2c.h" 
#include "wifi_connect.h"
#include "esp_task_wdt.h"
#include "esp_flash.h"
#include "mqtt_app.h"
#include "app_controller.h" 
#include "nextion.h" 

static const char *TAG = "MAIN";

// Definición de pines I2C (Solo para el sensor AS7265x)
#define I2C_MASTER_SCL_IO           22
#define I2C_MASTER_SDA_IO           21
#define I2C_MASTER_NUM              I2C_NUM_0
#define I2C_MASTER_FREQ_HZ          100000 

// Función auxiliar para actualizar barra de carga
void actualizar_carga(int porcentaje, const char* texto) {
    char cmd[30];
    
    // 1. Actualizar texto de status
    nextion_send_txt("page0.t0", texto);
    
    // 2. Actualizar barra de progreso
    snprintf(cmd, sizeof(cmd), "page0.j0.val=%d", porcentaje);
    nextion_send_cmd(cmd);
    
    // Pequeño delay para que el ojo humano vea el cambio
    vTaskDelay(pdMS_TO_TICKS(50));
}

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

    // 2. INSTALAR I2C (GLOBAL para el sensor)
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

    // 3. INICIAR PANTALLA NEXTION
    if (nextion_init() == ESP_OK) {
        ESP_LOGI(TAG, "Nextion UART: OK");
        
        // --- EFECTO 1: AMANECER (FADE-IN) MEJORADO ---
        
        // 1. Apagado total inicial y carga de página
        nextion_send_cmd("dim=0"); 
        nextion_send_cmd("page 0");
        
        // Esperamos medio segundo en negro para asegurar que la pantalla procesa el "page 0"
        // antes de empezar a subir el brillo. Esto evita parpadeos iniciales.
        vTaskDelay(pdMS_TO_TICKS(500)); 

        // 2. Subida lenta y suave (De 0 a 100 de 1 en 1)
        // Total tiempo: 100 pasos * 25ms = 2500ms (2.5 segundos)
        ESP_LOGI(TAG, "Iniciando Fade-In...");
        for(int i=0; i<=100; i += 5) {
            char dim_cmd[16];
            snprintf(dim_cmd, sizeof(dim_cmd), "dim=%d", i);
            nextion_send_cmd(dim_cmd);
            vTaskDelay(pdMS_TO_TICKS(25));
        }

        // Empezamos la barra al 10%
        actualizar_carga(10, "System Init...");
        
    } else {
        ESP_LOGE(TAG, "Nextion UART: FAIL");
    }

    // 4. INICIAR EL CEREBRO (APP CONTROLLER)
    actualizar_carga(30, "Init Controller...");
    if (app_controller_init() == ESP_OK) {
        ESP_LOGI(TAG, "App Controller: OK");
    } else {
        ESP_LOGE(TAG, "App Controller: FAIL");
    }

    // Variable para guardar el tamaño de la flash
    uint32_t flash_size;
    actualizar_carga(50, "Checking Flash...");
    if (esp_flash_get_size(NULL, &flash_size) == ESP_OK) {
        ESP_LOGI("SYSTEM", "Tamaño de Flash: %lu MB", flash_size / (1024 * 1024));
    } else {
        ESP_LOGE("SYSTEM", "No se pudo leer el tamaño de la flash");
    }

    // 5. CONECTAR WIFI Y MQTT
    actualizar_carga(60, "Connecting WiFi...");
    
    if (wifi_connect_init() == ESP_OK) {
        // Feedback positivo
        actualizar_carga(80, "WiFi Connected!");
        
        ESP_LOGI(TAG, "Iniciando MQTT...");
        mqtt_app_start();
        
        // 6. SINCRONIZACIÓN
        actualizar_carga(90, "Sync MQTT...");
        ESP_LOGI(TAG, "Esperando 5s para recibir configuración MQTT...");
        
        // Durante este delay, llenamos la barra lentamente hasta el final
        for(int i=90; i<=100; i++) {
             char j_cmd[24];
             snprintf(j_cmd, sizeof(j_cmd), "page0.j0.val=%d", i);
             nextion_send_cmd(j_cmd);
             vTaskDelay(pdMS_TO_TICKS(500)); // Repartimos los 5s aquí
        }

        // 7. DECISIÓN POR DEFECTO
        ESP_LOGW(TAG, "Enviando señal de arranque por defecto...");
        
        // ¡Listo! Al mandar GO_IDLE, el app_controller cambiará a la Page 1 (Menú)
        app_controller_send_event(APP_EVENT_GO_IDLE);

    } else {
        actualizar_carga(0, "Error WiFi");
        nextion_send_txt("page0.t0", "Fallo WiFi");
        ESP_LOGE(TAG, "Fallo crítico WiFi");
    }

    // El main termina aquí, pero la tarea de app_controller sigue viva.
}