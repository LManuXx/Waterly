# Waterly ESP32 Firmware

Firmware para ESP32 del sistema Waterly — análisis de calidad del agua mediante espectroscopía NIR.

## Componentes

| Componente | Descripción |
|------------|-------------|
| `app_controller` | Máquina de estados principal (IDLE, TRAINING, SLEEPING, UPDATING, SINGLE_MEASURE) |
| `as7265x` | Driver del sensor espectral AS7265x (18 canales UV-VIS-NIR) via I2C |
| `config_manager` | Gestión de configuración NVS (WiFi, MQTT, sensor, BLE, OTA) |
| `mqtt_app` | Cliente MQTT + parser de comandos + autodetección mDNS del broker |
| `nextion` | Driver de pantalla Nextion via UART |
| `wifi` | Conexión WiFi STA + BLE provisioning como fallback |
| `ota` | Actualizaciones OTA via HTTP manual (`esp_http_client` + `esp_ota_begin/write/end`) |

## Configuración NVS

Todos los parámetros se almacenan en NVS (namespace `waterly`). Se pueden modificar via MQTT:

```json
{"config": {"wifi_ssid": "MiRed", "wifi_pass": "1234", "sensor_gain": 3}}
```

Claves soportadas: `wifi_ssid`, `wifi_pass`, `mqtt_broker`, `mqtt_topic_cmd`, `mqtt_topic_dat`, `sensor_gain`, `sensor_integration`, `sensor_led_current`, `ble_pop`, `ota_url`, `factory_reset`.

**OTA URL por defecto**: `http://waterly.local:8000/firmware/version.json`

## Actualización OTA

El firmware soporta OTA via HTTP (sin TLS). El flujo:

1. Recibe `{"update": true}` via MQTT en `waterly/comandos`
2. Descarga `version.json` desde la URL configurada en NVS
3. Compara versiones (entero > entero)
4. Si hay update: descarga `.bin` via HTTP → `esp_ota_begin/write/end` → `esp_restart()`
5. Si no hay update: vuelve a IDLE sin reiniciar

**Nota**: Usa `app_update` component de ESP-IDF v5.x, NO `esp_app_update` ni `esp_https_ota`.

## Build & Flash

```bash
. ~/esp/v5.5.1/esp-idf/export.sh   # una vez por sesión
./compile.sh
./flash.sh
./monitor.sh
./clean.sh
```
