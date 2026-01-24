#ifndef AS7265X_H
#define AS7265X_H

#include "driver/i2c.h"
#include "esp_err.h"
#include <stdbool.h>

// Dirección I2C física del chip (siempre es 0x49)
#define AS7265X_I2C_ADDR 0x49

// Estructura de configuración
typedef struct {
    i2c_port_t i2c_port;
} as7265x_handle_t;

// Estructura de datos (18 canales)
typedef struct {
    float A, B, C, D, E, F; // UV (Slave 2)
    float G, H, I, J, K, L; // VIS (Slave 1)
    float R, S, T, U, V, W; // NIR (Master)
} as7265x_values_t;

// --- DEFINICIONES DE GANANCIA ---
typedef enum {
    AS7265X_GAIN_1X = 0,
    AS7265X_GAIN_37X = 1,
    AS7265X_GAIN_16X = 2,
    AS7265X_GAIN_64X = 3
} as7265x_gain_t;

// --- DEFINICIONES DE MODO (Nombres corregidos para tu app_controller) ---
typedef enum {
    AS7265X_MEASUREMENT_MODE_4_CHAN = 0,
    AS7265X_MEASUREMENT_MODE_4_CHAN_2 = 1,
    AS7265X_MEASUREMENT_MODE_6_CHAN_CONTINUOUS = 2,
    AS7265X_MEASUREMENT_MODE_6_CHAN_ONE_SHOT = 3
} as7265x_mode_t;

// --- PROTOTIPOS ---

/**
 * @brief Inicializa la comunicación con el chipset AS7265x.
 */
esp_err_t as7265x_init(as7265x_handle_t *handle, i2c_port_t port);

/**
 * @brief Comprueba si hay datos nuevos listos para leer.
 * Consulta el bit DATA_RDY del registro de configuración.
 */
bool as7265x_data_ready(as7265x_handle_t *handle);

/**
 * @brief Lee los 18 canales calibrados.
 * Maneja la conmutación de DEV_SEL automáticamente.
 */
esp_err_t as7265x_get_all_values(as7265x_handle_t *handle, as7265x_values_t *values);

/**
 * @brief Configura la bombilla (LED blanco/UV shutter)
 * current_code: 0=12.5mA, 1=25mA, 2=50mA, 3=100mA
 */
esp_err_t as7265x_set_bulb_current(as7265x_handle_t *handle, uint8_t current_code, bool enable);

/**
 * @brief Configura el LED indicador.
 */
esp_err_t as7265x_set_indicator_led(as7265x_handle_t *handle, bool enable, uint8_t current_code);

/**
 * @brief Establece ganancia y modo.
 */
esp_err_t as7265x_set_config(as7265x_handle_t *handle, as7265x_mode_t mode, as7265x_gain_t gain);

/**
 * @brief Establece tiempo de integración.
 * Time = value * 2.8ms.
 */
esp_err_t as7265x_set_integration_time(as7265x_handle_t *handle, uint8_t value);

#endif