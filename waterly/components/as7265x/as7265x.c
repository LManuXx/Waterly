#include "as7265x.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h> // Para memcpy

static const char *TAG = "AS7265X";

// Registros I2C Físicos
#define I2C_SLAVE_STATUS_REG  0x00
#define I2C_SLAVE_WRITE_REG   0x01
#define I2C_SLAVE_READ_REG    0x02

// Bits de estado
#define STATUS_TX_VALID       0x02
#define STATUS_RX_VALID       0x01

// Registros Virtuales
#define VIRT_HW_VERSION_H     0x00
#define VIRT_CONFIG           0x04
#define VIRT_INT_TIME         0x05
#define VIRT_LED_CONFIG       0x07
#define VIRT_DEV_SEL          0x4F 
#define VIRT_CALIBRATED_START 0x14 

#define I2C_TIMEOUT           (pdMS_TO_TICKS(200))

// --------------------------------------------------------------------------
// FUNCIONES DE BAJO NIVEL (I2C FÍSICO)
// --------------------------------------------------------------------------

static esp_err_t read_phy_reg(as7265x_handle_t *handle, uint8_t reg, uint8_t *val) {
    return i2c_master_write_read_device(handle->i2c_port, AS7265X_I2C_ADDR, 
                                        &reg, 1, val, 1, I2C_TIMEOUT);
}

static esp_err_t write_phy_reg(as7265x_handle_t *handle, uint8_t reg, uint8_t val) {
    uint8_t data[2] = {reg, val};
    return i2c_master_write_to_device(handle->i2c_port, AS7265X_I2C_ADDR, 
                                      data, 2, I2C_TIMEOUT);
}

// --------------------------------------------------------------------------
// GESTOR DE REGISTROS VIRTUALES
// --------------------------------------------------------------------------

static esp_err_t virtual_write(as7265x_handle_t *handle, uint8_t virtual_reg, uint8_t value) {
    uint8_t status;
    int retries;

    // 1. Esperar TX libre
    retries = 50;
    while (1) {
        if (read_phy_reg(handle, I2C_SLAVE_STATUS_REG, &status) != ESP_OK) return ESP_FAIL;
        if ((status & STATUS_TX_VALID) == 0) break;
        if (--retries == 0) return ESP_ERR_TIMEOUT;
        vTaskDelay(pdMS_TO_TICKS(2));
    }

    // 2. Enviar dirección (MSB=1 para write)
    if (write_phy_reg(handle, I2C_SLAVE_WRITE_REG, (virtual_reg | 0x80)) != ESP_OK) return ESP_FAIL;

    // 3. Esperar TX libre
    retries = 50;
    while (1) {
        if (read_phy_reg(handle, I2C_SLAVE_STATUS_REG, &status) != ESP_OK) return ESP_FAIL;
        if ((status & STATUS_TX_VALID) == 0) break;
        if (--retries == 0) return ESP_ERR_TIMEOUT;
        vTaskDelay(pdMS_TO_TICKS(2));
    }

    // 4. Escribir valor
    return write_phy_reg(handle, I2C_SLAVE_WRITE_REG, value);
}

static esp_err_t virtual_read(as7265x_handle_t *handle, uint8_t virtual_reg, uint8_t *val) {
    uint8_t status;
    int retries;

    // 1. Esperar TX libre
    retries = 50;
    while (1) {
        if (read_phy_reg(handle, I2C_SLAVE_STATUS_REG, &status) != ESP_OK) return ESP_FAIL;
        if ((status & STATUS_TX_VALID) == 0) break;
        if (--retries == 0) return ESP_ERR_TIMEOUT;
        vTaskDelay(pdMS_TO_TICKS(2));
    }

    // 2. Enviar dirección (MSB=0 para read)
    if (write_phy_reg(handle, I2C_SLAVE_WRITE_REG, virtual_reg) != ESP_OK) return ESP_FAIL;

    // 3. Esperar RX valida
    retries = 50;
    while (1) {
        if (read_phy_reg(handle, I2C_SLAVE_STATUS_REG, &status) != ESP_OK) return ESP_FAIL;
        if ((status & STATUS_RX_VALID) != 0) break;
        if (--retries == 0) return ESP_ERR_TIMEOUT;
        vTaskDelay(pdMS_TO_TICKS(2));
    }

    // 4. Leer dato
    return read_phy_reg(handle, I2C_SLAVE_READ_REG, val);
}

static float read_float_data(as7265x_handle_t *handle, uint8_t reg_base) {
    uint8_t b0, b1, b2, b3;
    if(virtual_read(handle, reg_base + 0, &b0) != ESP_OK) return 0.0f;
    if(virtual_read(handle, reg_base + 1, &b1) != ESP_OK) return 0.0f;
    if(virtual_read(handle, reg_base + 2, &b2) != ESP_OK) return 0.0f;
    if(virtual_read(handle, reg_base + 3, &b3) != ESP_OK) return 0.0f;

    uint32_t val_u32 = (b0 << 24) | (b1 << 16) | (b2 << 8) | b3;
    float result;
    memcpy(&result, &val_u32, sizeof(float));
    return result;
}

// --------------------------------------------------------------------------
// FUNCIONES PÚBLICAS
// --------------------------------------------------------------------------

esp_err_t as7265x_init(as7265x_handle_t *handle, i2c_port_t port) {
    handle->i2c_port = port;
    uint8_t hw_type;

    int retries = 5;
    while (retries > 0) {
        if (virtual_read(handle, VIRT_HW_VERSION_H, &hw_type) == ESP_OK) {
            if (hw_type == 0x40) break;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
        retries--;
    }

    if (retries == 0) {
        ESP_LOGE(TAG, "AS7265x no encontrado.");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "AS7265x detectado (Type: 0x%02X).", hw_type);

    // Soft Reset
    virtual_write(handle, VIRT_CONFIG, 0x80);
    vTaskDelay(pdMS_TO_TICKS(1200)); 

    as7265x_set_config(handle, AS7265X_MEASUREMENT_MODE_6_CHAN_CONTINUOUS, AS7265X_GAIN_64X);
    as7265x_set_integration_time(handle, 50);
    
    return ESP_OK;
}

// IMPLEMENTACIÓN DE LA FUNCIÓN QUE FALTABA
bool as7265x_data_ready(as7265x_handle_t *handle) {
    uint8_t val;
    // Leer registro de configuración (0x04)
    if (virtual_read(handle, VIRT_CONFIG, &val) != ESP_OK) return false;
    // Bit 1 = DATA_RDY
    return (val & 0x02) != 0;
}

esp_err_t as7265x_get_all_values(as7265x_handle_t *handle, as7265x_values_t *values) {
    // Nota: Es mejor chequear data_ready fuera de esta función o usarla aquí
    // Si usas polling fuera, aquí asumimos que ya toca leer.
    
    // MASTER (NIR)
    if(virtual_write(handle, VIRT_DEV_SEL, 0x00) != ESP_OK) return ESP_FAIL;
    values->R = read_float_data(handle, VIRT_CALIBRATED_START);      
    values->S = read_float_data(handle, VIRT_CALIBRATED_START + 4);  
    values->T = read_float_data(handle, VIRT_CALIBRATED_START + 8);  
    values->U = read_float_data(handle, VIRT_CALIBRATED_START + 12); 
    values->V = read_float_data(handle, VIRT_CALIBRATED_START + 16); 
    values->W = read_float_data(handle, VIRT_CALIBRATED_START + 20); 

    // SLAVE 1 (VIS)
    if(virtual_write(handle, VIRT_DEV_SEL, 0x01) != ESP_OK) return ESP_FAIL;
    values->G = read_float_data(handle, VIRT_CALIBRATED_START);      
    values->H = read_float_data(handle, VIRT_CALIBRATED_START + 4);
    values->I = read_float_data(handle, VIRT_CALIBRATED_START + 8);
    values->J = read_float_data(handle, VIRT_CALIBRATED_START + 12);
    values->K = read_float_data(handle, VIRT_CALIBRATED_START + 16);
    values->L = read_float_data(handle, VIRT_CALIBRATED_START + 20);

    // SLAVE 2 (UV)
    if(virtual_write(handle, VIRT_DEV_SEL, 0x02) != ESP_OK) return ESP_FAIL;
    values->A = read_float_data(handle, VIRT_CALIBRATED_START);
    values->B = read_float_data(handle, VIRT_CALIBRATED_START + 4);
    values->C = read_float_data(handle, VIRT_CALIBRATED_START + 8);
    values->D = read_float_data(handle, VIRT_CALIBRATED_START + 12);
    values->E = read_float_data(handle, VIRT_CALIBRATED_START + 16);
    values->F = read_float_data(handle, VIRT_CALIBRATED_START + 20);

    ESP_LOGI(TAG, "====== LECTURA ESPECTRAL (18 CANALES) ======");

    // Nota: Como 'values' es un puntero, se usa '->' para acceder a los miembros
    ESP_LOGI(TAG, "[UV] A:%.2f | B:%.2f | C:%.2f | D:%.2f | E:%.2f | F:%.2f",
             values->A, values->B, values->C, values->D, values->E, values->F);

    ESP_LOGI(TAG, "[VIS] G:%.2f | H:%.2f | I:%.2f | J:%.2f | K:%.2f | L:%.2f",
             values->G, values->H, values->I, values->J, values->K, values->L);

    ESP_LOGI(TAG, "[NIR] R:%.2f | S:%.2f | T:%.2f | U:%.2f | V:%.2f | W:%.2f",
             values->R, values->S, values->T, values->U, values->V, values->W);

    ESP_LOGI(TAG, "============================================");

    return ESP_OK;
}

esp_err_t as7265x_set_config(as7265x_handle_t *handle, as7265x_mode_t mode, as7265x_gain_t gain) {
    uint8_t current_val;
    if (virtual_read(handle, VIRT_CONFIG, &current_val) != ESP_OK) return ESP_FAIL;
    
    current_val &= ~(0b00111100); 
    current_val |= (mode << 2);
    current_val |= (gain << 4);
    
    return virtual_write(handle, VIRT_CONFIG, current_val);
}

esp_err_t as7265x_set_integration_time(as7265x_handle_t *handle, uint8_t value) {
    return virtual_write(handle, VIRT_INT_TIME, value);
}

esp_err_t as7265x_set_bulb_current(as7265x_handle_t *handle, uint8_t current_code, bool enable) {
    uint8_t led_cfg;
    virtual_write(handle, VIRT_DEV_SEL, 0x00); 
    if (virtual_read(handle, VIRT_LED_CONFIG, &led_cfg) != ESP_OK) return ESP_FAIL;

    led_cfg &= ~(0b00111000);
    if (enable) {
        led_cfg |= (1 << 3); 
        led_cfg |= (current_code << 4);
    }
    return virtual_write(handle, VIRT_LED_CONFIG, led_cfg);
}

esp_err_t as7265x_set_indicator_led(as7265x_handle_t *handle, bool enable, uint8_t current_code) {
    uint8_t led_cfg;
    virtual_write(handle, VIRT_DEV_SEL, 0x00); 
    if (virtual_read(handle, VIRT_LED_CONFIG, &led_cfg) != ESP_OK) return ESP_FAIL;

    led_cfg &= ~(0b00000111);
    if (enable) {
        led_cfg |= 1; 
        led_cfg |= (current_code << 1); 
    }
    return virtual_write(handle, VIRT_LED_CONFIG, led_cfg);
}