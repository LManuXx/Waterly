idf_component_register(SRCS "ota_update.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_http_client app_update json)
