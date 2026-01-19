#include <string.h>
#include "driver/i2c.h"
#include "esp_log.h"
#include "ssd1306.h"
#include "font8x8_basic.h"

#define OLED_ADDR 0x3C
static const char *TAG = "SSD1306";
static i2c_port_t s_i2c_port; 

// CONFIGURACIÓN DE PANTALLA

#define CONFIG_SEG_REMAP     0xA1

#define CONFIG_COM_SCAN      0xC8

static void ssd1306_send_cmd(uint8_t cmd) {
    i2c_cmd_handle_t link = i2c_cmd_link_create();
    i2c_master_start(link);
    i2c_master_write_byte(link, (OLED_ADDR << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(link, 0x00, true);
    i2c_master_write_byte(link, cmd, true);
    i2c_master_stop(link);
    i2c_master_cmd_begin(s_i2c_port, link, pdMS_TO_TICKS(50));
    i2c_cmd_link_delete(link);
}

static void ssd1306_send_data(uint8_t *data, int len) {
    i2c_cmd_handle_t link = i2c_cmd_link_create();
    i2c_master_start(link);
    i2c_master_write_byte(link, (OLED_ADDR << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(link, 0x40, true);
    i2c_master_write(link, data, len, true);
    i2c_master_stop(link);
    i2c_master_cmd_begin(s_i2c_port, link, pdMS_TO_TICKS(50));
    i2c_cmd_link_delete(link);
}

void ssd1306_init(i2c_port_t i2c_num) {
    s_i2c_port = i2c_num;
    ESP_LOGI(TAG, "Iniciando OLED...");

    uint8_t init_cmds[] = {
        0xAE,       // Display OFF
        0xD5, 0x80, // Clock div
        0xA8, 0x3F, // Multiplex (64 líneas)
        0xD3, 0x00, // Offset 0
        0x40,       // Start Line 0
        0x8D, 0x14, // Charge Pump (Enable)
        0x20, 0x02, // Page Addressing Mode
        
        CONFIG_SEG_REMAP,    
        CONFIG_COM_SCAN,     
        
        0xDA, 0x12, // COM Pins Config
        0x81, 0xCF, // Contrast
        0xD9, 0xF1, // Pre-charge
        0xDB, 0x40, // VCOMH Deselect
        0xA4,       // Entire Display ON (Resume)
        0xA6,       // Normal Display
        0x2E,       // Deactivate Scroll
        0xAF        // Display ON
    };

    for (int i = 0; i < sizeof(init_cmds); i++) ssd1306_send_cmd(init_cmds[i]);
    ssd1306_clear();
}

void ssd1306_clear(void) {
    uint8_t zero[132];
    memset(zero, 0x00, 132);
    for (uint8_t page = 0; page < 8; page++) {
        ssd1306_send_cmd(0xB0 + page);
        ssd1306_send_cmd(0x00);      
        ssd1306_send_cmd(0x10); 
        ssd1306_send_data(zero, 132);
    }
}

// --- FUNCIÓN MÁGICA: Transpone los bits de Horizontal a Vertical ---
void ssd1306_print(int page, int col, char *text) {
    int len = strlen(text);
    if (page > 7) page = 7;
    if (col > 127) col = 127;

    ssd1306_send_cmd(0xB0 + page);
    ssd1306_send_cmd(0x00 + (col & 0x0F));      
    ssd1306_send_cmd(0x10 + ((col >> 4) & 0x0F)); 

    for (int i = 0; i < len; i++) {
        // Obtenemos la letra original
        uint8_t *src = (uint8_t*)font8x8_basic[(int)text[i]];
        
        // Creamos un buffer para la letra de pie
        uint8_t dst[8] = {0};

        // Algoritmo de Rotación 90 grados (Transposición de matriz 8x8)
        for (int r = 0; r < 8; r++) {      // Recorremos filas originales
            for (int c = 0; c < 8; c++) {  // Recorremos bits de cada fila
                if (src[r] & (1 << c)) {   // Si el bit está encendido...
                    dst[c] |= (1 << r);    // ...lo ponemos en la columna correspondiente
                }
            }
        }
        
        // Enviamos la letra ya rotada
        ssd1306_send_data(dst, 8);
    }
}