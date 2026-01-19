idf_component_register(SRCS "ota_update.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_https_ota mbedtls nvs_flash esp_event json)