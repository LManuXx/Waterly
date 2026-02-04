#ifndef NEXTION_H
#define NEXTION_H

#include "esp_err.h"

// --- CONFIGURACIÓN DE PINES ---
// Puedes cambiarlos aquí si decides usar otros
#define NEXTION_TX_PIN      17  // Al cable AMARILLO (RX) de la pantalla
#define NEXTION_RX_PIN      16  // Al cable AZUL (TX) de la pantalla
#define NEXTION_UART_NUM    UART_NUM_2
#define NEXTION_BAUD_RATE   9600

/**
 * @brief Inicializa la UART y arranca la tarea de escucha en segundo plano.
 * @return ESP_OK o ESP_FAIL
 */
esp_err_t nextion_init(void);

/**
 * @brief Envía un comando directo a la pantalla (ej: "page 1", "sleep=1")
 */
void nextion_send_cmd(const char* cmd);

/**
 * @brief Actualiza el texto de un objeto en la pantalla.
 * @param obj_name Nombre del objeto en el editor (ej: "t0", "t_uv")
 * @param text El texto o número a mostrar
 */
void nextion_send_txt(const char* obj_name, const char* text);

#endif // NEXTION_H