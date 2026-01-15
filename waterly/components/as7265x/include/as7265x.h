#ifndef AS7265X_H
#define AS7265X_H

#include <stdint.h>
#include "esp_err.h"
#include "driver/i2c.h"

// Dirección I2C predeterminada del AS72651 (Master) [cite: 551]
#define AS7265X_I2C_ADDR 0x49

// Estructura para almacenar los valores calibrados (float) de los 18 canales
// Los canales van desde UV hasta NIR [cite: 8]
typedef struct {
    float A; // 410nm
    float B; // 435nm
    float C; // 460nm
    float D; // 485nm
    float E; // 510nm
    float F; // 535nm
    float G; // 560nm
    float H; // 585nm
    float I; // 645nm (Datasheet gap noted)
    float J; // 705nm
    float K; // 900nm
    float L; // 940nm
    float R; // 610nm
    float S; // 680nm
    float T; // 730nm
    float U; // 760nm
    float V; // 810nm
    float W; // 860nm
} as7265x_values_t;

typedef enum {
    AS7265X_GAIN_1X = 0,
    AS7265X_GAIN_3_7X = 1,
    AS7265X_GAIN_16X = 2,
    AS7265X_GAIN_64X = 3
} as7265x_gain_t;

typedef enum {
    AS7265X_MEASUREMENT_MODE_4_CHAN = 0,
    AS7265X_MEASUREMENT_MODE_4_CHAN_2 = 1,
    AS7265X_MEASUREMENT_MODE_6_CHAN_CONTINUOUS = 2, // Lee los 6 canales de cada sensor (18 total) continuamente
    AS7265X_MEASUREMENT_MODE_6_CHAN_ONE_SHOT = 3    // Lee una vez y espera
} as7265x_mode_t;

typedef struct {
    i2c_port_t i2c_port;
} as7265x_handle_t;

/**
 * @brief Inicializa el sensor verificando la conexión
 */
esp_err_t as7265x_init(as7265x_handle_t *handle, i2c_port_t port);

/**
 * @brief Configura la ganancia y el modo de medición
 * Registro 0x04 [cite: 703]
 */
esp_err_t as7265x_set_config(as7265x_handle_t *handle, as7265x_mode_t mode, as7265x_gain_t gain);

/**
 * @brief Configura el tiempo de integración
 * Valor * 2.8ms = Tiempo real [cite: 711]
 */
esp_err_t as7265x_set_integration_time(as7265x_handle_t *handle, uint8_t value);

/**
 * @brief Controla el LED de iluminación (White LED)
 * [cite: 724]
 */
esp_err_t as7265x_set_bulb_current(as7265x_handle_t *handle, uint8_t current_code, bool enable);

/**
 * @brief Lee todos los valores calibrados (floats)
 * Convierte automáticamente de IEEE 754 Big Endian a float nativo del ESP32.
 */
esp_err_t as7265x_get_all_values(as7265x_handle_t *handle, as7265x_values_t *values);

bool as7265x_data_ready(as7265x_handle_t *handle);

#endif // AS7265X_H