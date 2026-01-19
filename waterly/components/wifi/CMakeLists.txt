idf_component_register(SRCS "wifi_connect.c"
                       INCLUDE_DIRS "include"
                       REQUIRES freertos esp_wifi nvs_flash esp_event esp_netif lwip wifi_provisioning)