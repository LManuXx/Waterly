#ifndef OTA_UPDATE_H
#define OTA_UPDATE_H
#include "esp_err.h"

// Checkea el JSON y actualiza si es necesario
esp_err_t check_and_update_firmware(const char *json_url, int current_version);

#endif