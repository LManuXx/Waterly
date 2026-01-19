#ifndef SSD1306_H
#define SSD1306_H

#include <stdint.h>
#include "driver/i2c.h"

// Inicializa la pantalla pasando el puerto I2C que ya configuraste en el main
void ssd1306_init(i2c_port_t i2c_num);

// Borra todo el contenido (pone la pantalla en negro)
void ssd1306_clear(void);

// Escribe texto en una posición específica
// page: Fila (0 a 7)
// col:  Columna (0 a 127)
// text: El string a escribir
void ssd1306_print(int page, int col, char *text);

#endif