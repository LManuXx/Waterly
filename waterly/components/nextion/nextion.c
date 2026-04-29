#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "nextion.h"

// Incluimos tu controlador para poder enviarle órdenes
#include "app_controller.h"

static const char *TAG = "NEXTION";
static const int RX_BUF_SIZE = 1024;

// --- FUNCIONES DE ENVÍO (TX) ---

void nextion_send_cmd(const char* cmd) {
    if (cmd == NULL) return;
    
    // 1. Enviar el comando
    uart_write_bytes(NEXTION_UART_NUM, cmd, strlen(cmd));
    
    // 2. Enviar el terminador obligatorio (0xFF 0xFF 0xFF)
    const char terminator[] = {0xFF, 0xFF, 0xFF};
    uart_write_bytes(NEXTION_UART_NUM, terminator, 3);
}

void nextion_send_txt(const char* obj_name, const char* text) {
    char buffer[256];
    // Formato Nextion: objeto.txt="texto"
    snprintf(buffer, sizeof(buffer), "%s.txt=\"%s\"", obj_name, text);
    nextion_send_cmd(buffer);
}

void nextion_set_progress_bar(const char* obj_name, int value) {
    char buffer[64];
    // Formato Nextion para valores numéricos: objeto.val=100
    if (value < 0) value = 0;
    if (value > 100) value = 100;
    snprintf(buffer, sizeof(buffer), "%s.val=%d", obj_name, value);
    nextion_send_cmd(buffer);
}

// --- TAREA DE RECEPCIÓN (RX) ---
// Escucha lo que pulsas en la pantalla
static void nextion_rx_task(void *arg) {
    uint8_t* data = (uint8_t*) malloc(RX_BUF_SIZE);
    
    ESP_LOGI(TAG, "Tarea de escucha iniciada...");

    while (1) {
        // Leemos UART con timeout (para no bloquear la CPU al 100%)
        const int rxBytes = uart_read_bytes(NEXTION_UART_NUM, data, RX_BUF_SIZE, 100 / portTICK_PERIOD_MS);
        
        if (rxBytes > 0) {
            // Buscamos el patrón de evento de botón: 0x65
            // Formato estándar: 0x65 [Página] [ID] [Estado] ...
            
            for (int i = 0; i < rxBytes - 3; i++) {
                // Verificamos cabecera (0x65) y que sea evento de SOLTAR (Release = 0x00)
                // Usamos Release para evitar rebotes o dobles pulsaciones
                if (data[i] == 0x65 && data[i+3] == 0x00) {
                    
                    uint8_t page = data[i+1];
                    uint8_t id   = data[i+2];

                    // Filtramos por página (Tus botones están en Page 1 según me dijiste)
                    if (page == 1) {
                        switch (id) {
                            case NEXTION_BTN_ID_IDLE:
                                ESP_LOGI(TAG, "Boton IDLE presionado");
                                app_controller_send_event(APP_EVENT_GO_IDLE);
                                break;

                            case NEXTION_BTN_ID_SCAN:
                                ESP_LOGI(TAG, "Boton SCAN presionado");
                                app_controller_send_event(APP_EVENT_SINGLE_MEASURE);
                                break;

                            case NEXTION_BTN_ID_TRAIN:
                                ESP_LOGI(TAG, "Boton TRAIN presionado (Continuo)");
                                app_controller_send_event(APP_EVENT_START_TRAINING);
                                break;

                            case NEXTION_BTN_ID_OTA:
                                ESP_LOGI(TAG, "Boton OTA presionado");
                                app_controller_send_event(APP_EVENT_START_OTA);
                                break;

                            case NEXTION_BTN_ID_SLEEP:
                                ESP_LOGI(TAG, "Boton SLEEP presionado");
                                app_controller_send_event(APP_EVENT_STOP_AND_SLEEP);
                                break;

                            case NEXTION_BTN_ID_RESET:
                                ESP_LOGW(TAG, "Boton RESET -> Reiniciando ESP32...");
                                esp_restart();
                                break;
                                
                            case NEXTION_BTN_ID_WIFI_CHECK:
                                ESP_LOGI(TAG, "Boton WIFI CHECK presionado");
                                app_controller_send_event(APP_EVENT_CHECK_WIFI);
                                break;

                            default:
                                ESP_LOGW(TAG, "Boton desconocido: Pag %d ID %d", page, id);
                                break;
                        }
                    }
                    // Saltamos bytes para no volver a leer el mismo evento
                    i += 6; 
                }
            }
        }
    }
    free(data);
    vTaskDelete(NULL);
}

// --- INICIALIZACIÓN ---
esp_err_t nextion_init(void) {
    const uart_config_t uart_config = {
        .baud_rate = NEXTION_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };

    // Instalamos driver UART
    // RX Buffer: 2048 (bastante espacio), TX Buffer: 0 (bloqueante, más simple)
    esp_err_t err = uart_driver_install(NEXTION_UART_NUM, RX_BUF_SIZE * 2, 0, 0, NULL, 0);
    if (err != ESP_OK) return err;

    ESP_ERROR_CHECK(uart_param_config(NEXTION_UART_NUM, &uart_config));
    ESP_ERROR_CHECK(uart_set_pin(NEXTION_UART_NUM, NEXTION_TX_PIN, NEXTION_RX_PIN, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    // Creamos la tarea que escuchará los botones
    xTaskCreate(nextion_rx_task, "nextion_rx", 4096, NULL, 5, NULL);

    ESP_LOGI(TAG, "Nextion Iniciada en UART2 (TX:%d RX:%d)", NEXTION_TX_PIN, NEXTION_RX_PIN);
    return ESP_OK;
}