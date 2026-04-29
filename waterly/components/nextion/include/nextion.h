#ifndef NEXTION_H
#define NEXTION_H

#include "esp_err.h"

// --- CONFIGURACIÓN DE PINES ---
// Puedes cambiarlos aquí si decides usar otros
#define NEXTION_TX_PIN      17  // Al cable AMARILLO (RX) de la pantalla
#define NEXTION_RX_PIN      16  // Al cable AZUL (TX) de la pantalla
#define NEXTION_UART_NUM    UART_NUM_2
#define NEXTION_BAUD_RATE   9600

// --- IDS DE BOTONES DE LA PANTALLA ---
// Estos IDs se generan en Nextion Editor cuando creas un botón.
// Si eliminas botones y creas nuevos, los IDs pueden cambiar. Cámbialos aquí si es necesario.
#define NEXTION_BTN_ID_IDLE       0x01
#define NEXTION_BTN_ID_OTA        0x02
#define NEXTION_BTN_ID_SCAN       0x04
#define NEXTION_BTN_ID_TRAIN      0x05
#define NEXTION_BTN_ID_SLEEP      0x06
#define NEXTION_BTN_ID_RESET      0x07
#define NEXTION_BTN_ID_WIFI_CHECK 0x08

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

/**
 * @brief Actualiza una barra de progreso en la pantalla.
 * @param obj_name Nombre de la barra de progreso (ej: "j0")
 * @param value Valor de 0 a 100
 */
void nextion_set_progress_bar(const char* obj_name, int value);

#endif // NEXTION_H