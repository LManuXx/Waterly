idf_component_register(SRCS "app_controller.c"
                       INCLUDE_DIRS "include"
                       REQUIRES as7265x ssd1306 mqtt_app ota)