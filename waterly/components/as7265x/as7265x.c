#include "as7265x.h"
#include "esp_log.h"
#include <string.h>
#include <math.h>

static const char *TAG = "AS7265X";

#define I2C_AS72XX_SLAVE_STATUS_REG 0x00
#define I2C_AS72XX_SLAVE_WRITE_REG  0x01
#define I2C_AS72XX_SLAVE_READ_REG   0x02

#define I2C_AS72XX_SLAVE_TX_VALID   0x02
#define I2C_AS72XX_SLAVE_RX_VALID   0x01
// Definición necesaria para el selector
#define VIRTUAL_DEV_SEL 0x4F

// Mapeo LINEAL (Estándar de Firmware AS7265x)
// AS72651 (NIR): 0x14 - 0x2B
// AS72652 (Color): 0x2C - 0x43
// AS72653 (UV): 0x44 - 0x5B

static esp_err_t read_phy_reg(as7265x_handle_t *handle, uint8_t reg, uint8_t *val) {
    // CAMBIO: Aumentado timeout a 1000 ticks
    return i2c_master_write_read_device(handle->i2c_port, AS7265X_I2C_ADDR, &reg, 1, val, 1, pdMS_TO_TICKS(1000));
}

static esp_err_t write_phy_reg(as7265x_handle_t *handle, uint8_t reg, uint8_t val) {
    uint8_t data[2] = {reg, val};
    // CAMBIO: Aumentado timeout a 1000 ticks
    return i2c_master_write_to_device(handle->i2c_port, AS7265X_I2C_ADDR, data, 2, pdMS_TO_TICKS(1000));
}

// Escritura Virtual Robusta
static esp_err_t virtual_write(as7265x_handle_t *handle, uint8_t virtual_reg, uint8_t value) {
    uint8_t status;
    int timeout = 100; // 100 intentos
    while (1) {
        if (read_phy_reg(handle, I2C_AS72XX_SLAVE_STATUS_REG, &status) != ESP_OK) return ESP_FAIL;
        if ((status & I2C_AS72XX_SLAVE_TX_VALID) == 0) break; // Buffer vacío, podemos escribir
        if (--timeout == 0) return ESP_ERR_TIMEOUT;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    if (write_phy_reg(handle, I2C_AS72XX_SLAVE_WRITE_REG, virtual_reg | 0x80) != ESP_OK) return ESP_FAIL;
    
    timeout = 100;
    while (1) {
        if (read_phy_reg(handle, I2C_AS72XX_SLAVE_STATUS_REG, &status) != ESP_OK) return ESP_FAIL;
        if ((status & I2C_AS72XX_SLAVE_TX_VALID) == 0) break;
        if (--timeout == 0) return ESP_ERR_TIMEOUT;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    return write_phy_reg(handle, I2C_AS72XX_SLAVE_WRITE_REG, value);
}

// Lectura Virtual Robusta
static esp_err_t virtual_read(as7265x_handle_t *handle, uint8_t virtual_reg, uint8_t *val) {
    uint8_t status;
    int timeout = 100;
    
    // 1. Esperar TX vacio
    while (1) {
        if (read_phy_reg(handle, I2C_AS72XX_SLAVE_STATUS_REG, &status) != ESP_OK) return ESP_FAIL;
        if ((status & I2C_AS72XX_SLAVE_TX_VALID) == 0) break;
        if (--timeout == 0) return ESP_ERR_TIMEOUT;
        vTaskDelay(pdMS_TO_TICKS(2));
    }

    // 2. Pedir lectura (MSB 0)
    if (write_phy_reg(handle, I2C_AS72XX_SLAVE_WRITE_REG, virtual_reg) != ESP_OK) return ESP_FAIL;

    // 3. Esperar RX lleno (Dato listo)
    timeout = 200; // Más tiempo para lectura
    while (1) {
        if (read_phy_reg(handle, I2C_AS72XX_SLAVE_STATUS_REG, &status) != ESP_OK) return ESP_FAIL;
        if ((status & I2C_AS72XX_SLAVE_RX_VALID) != 0) break; // ¡Dato listo!
        if (--timeout == 0) return ESP_ERR_TIMEOUT;
        vTaskDelay(pdMS_TO_TICKS(2));
    }

    // 4. Leer dato
    return read_phy_reg(handle, I2C_AS72XX_SLAVE_READ_REG, val);
}

esp_err_t as7265x_init(as7265x_handle_t *handle, i2c_port_t port) {
    handle->i2c_port = port;
    uint8_t type;
    // HW Version (0x00) debe ser 0x40
    if (virtual_read(handle, 0x00, &type) != ESP_OK) return ESP_FAIL;
    ESP_LOGI(TAG, "HW Type: 0x%02X", type);
    
    // HW Version L (0x01) suele ser 0x41
    uint8_t version;
    virtual_read(handle, 0x01, &version);
    ESP_LOGI(TAG, "HW Version: 0x%02X", version);
    
    return ESP_OK;
}

esp_err_t as7265x_set_config(as7265x_handle_t *handle, as7265x_mode_t mode, as7265x_gain_t gain) {
    uint8_t val = (gain << 4) | (mode << 2);
    return virtual_write(handle, 0x04, val);
}

esp_err_t as7265x_set_integration_time(as7265x_handle_t *handle, uint8_t value) {
    return virtual_write(handle, 0x05, value);
}


esp_err_t as7265x_set_bulb_current(as7265x_handle_t *handle, uint8_t current_code, bool enable) {
    uint8_t val = (current_code << 4);
    if (enable) val |= (1 << 3); // Bit 3 es Enable

    // Iteramos por los 3 dispositivos (0=Master, 1=Slave1, 2=Slave2)
    // para encender/apagar sus respectivos LEDs.
    for (uint8_t dev = 0; dev < 3; dev++) {
        // 1. Seleccionar dispositivo
        if (virtual_write(handle, VIRTUAL_DEV_SEL, dev) != ESP_OK) return ESP_FAIL;
        
        // 2. Leer configuración actual del LED (para no borrar otros bits)
        uint8_t current_reg;
        if (virtual_read(handle, 0x07, &current_reg) != ESP_OK) return ESP_FAIL;
        
        // 3. Modificar solo los bits de corriente y enable
        current_reg &= ~(0b00111000); 
        current_reg |= val;
        
        // 4. Escribir configuración
        if (virtual_write(handle, 0x07, current_reg) != ESP_OK) return ESP_FAIL;
    }

    // IMPORTANTE: Volver a seleccionar el Master (0) al final.
    // Si no hacemos esto, la función de lectura lineal fallará porque esperará leer desde el esclavo 2.
    return virtual_write(handle, VIRTUAL_DEV_SEL, 0x00);
}

// Función auxiliar para imprimir HEX y convertir IEEE 754
static float read_channel(as7265x_handle_t *handle, uint8_t reg_base, const char* ch_name) {
    uint8_t b0, b1, b2, b3;
    
    // Lectura secuencial manual
    virtual_read(handle, reg_base, &b0);
    virtual_read(handle, reg_base + 1, &b1);
    virtual_read(handle, reg_base + 2, &b2);
    virtual_read(handle, reg_base + 3, &b3);

    uint32_t raw = (b0 << 24) | (b1 << 16) | (b2 << 8) | b3;
    
    float f;
    memcpy(&f, &raw, sizeof(f));
    
    // LOG DE DEPURACIÓN: ESTO ES LO IMPORTANTE
    // Si ves "FFFFFFFF" es error de bus. Si ves "00000000" es cero.
    // Si ves números como "3F80..." son floats válidos.
    ESP_LOGI(TAG, "CH %s [Reg 0x%02X]: HEX[%02X %02X %02X %02X] -> Float: %f", 
             ch_name, reg_base, b0, b1, b2, b3, f);

    return f;
}

esp_err_t as7265x_get_all_values(as7265x_handle_t *handle, as7265x_values_t *values) {
    // IMPORTANTE: Aseguramos estar en el banco del Maestro (0)
    // por si acaso se quedó cambiado en alguna prueba anterior.
    virtual_write(handle, 0x4F, 0x00); 

    // BLOQUE 1: NIR (Master) [0x14 - 0x28]
    // Estos siempre te funcionaban bien.
    values->R = read_channel(handle, 0x14, "R (610)");
    values->S = read_channel(handle, 0x18, "S (680)");
    values->T = read_channel(handle, 0x1C, "T (730)");
    values->U = read_channel(handle, 0x20, "U (760)");
    values->V = read_channel(handle, 0x24, "V (810)");
    values->W = read_channel(handle, 0x28, "W (860)");

    // PAUSA RESPIRATORIA: Damos 10ms al sensor para preparar los datos del siguiente chip
    vTaskDelay(pdMS_TO_TICKS(10));

    // BLOQUE 2: VISIBLE (Slave 1) [0x2C - 0x40]
    // Aquí es donde antes se cortaba después del G.
    values->G = read_channel(handle, 0x2C, "G (560)");
    values->H = read_channel(handle, 0x30, "H (585)");
    values->I = read_channel(handle, 0x34, "I (645)");
    values->J = read_channel(handle, 0x38, "J (705)");
    values->K = read_channel(handle, 0x3C, "K (900)");
    values->L = read_channel(handle, 0x40, "L (940)");

    // PAUSA RESPIRATORIA
    vTaskDelay(pdMS_TO_TICKS(10));

    // BLOQUE 3: UV (Slave 2) [0x44 - 0x58]
    values->A = read_channel(handle, 0x44, "A (410)");
    values->B = read_channel(handle, 0x48, "B (435)");
    values->C = read_channel(handle, 0x4C, "C (460)");
    values->D = read_channel(handle, 0x50, "D (485)");
    values->E = read_channel(handle, 0x54, "E (510)");
    values->F = read_channel(handle, 0x58, "F (535)");

    return ESP_OK;
}

// En as7265x.c
bool as7265x_data_ready(as7265x_handle_t *handle) {
    uint8_t val;
    // Leer registro 4 (Configuración)
    virtual_read(handle, 0x04, &val);
    // El bit 1 es DATA_RDY
    return (val & 0x02) != 0;
}