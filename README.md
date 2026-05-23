# Waterly

Sistema IoT de análisis de calidad del agua mediante espectroscopía NIR y machine learning para predecir la concentración de nitratos en muestras de agua.

## ¿Qué es?

Waterly utiliza un sensor espectral AS7265x (18 canales, UV-VIS-NIR) controlado por un ESP32 para medir la absorbancia de muestras de agua. El flujo de trabajo es:

1. **Calibración** — Se coloca agua limpia (0 nitratos) como referencia ("blank").
2. **Entrenamiento** — Se preparan muestras con concentraciones conocidas de nitratos (ej: 5, 10, 20 mg/L) y se toman lecturas espectrales para crear un dataset.
3. **Modelo ML** — Se entrena un modelo (DEVIATION o PLSR) con los datos recopilados.
4. **Predicción** — Se coloca una muestra desconocida y el sistema predice su concentración de nitratos.

## Arquitectura

```
┌─────────────┐     MQTT      ┌──────────────┐     RPC      ┌──────────────┐
│   ESP32     │ ────────────► │  Python API  │ ◄─────────── │ ThingsBoard  │
│  (sensor)   │ ◄──────────── │  (backend)   │ ───────────► │  (dashboard) │
└─────────────┘     MQTT      └──────┬───────┘              └──────────────┘
                                     │
                              ┌──────┴───────┐
                              │  InfluxDB    │
                              │  (time-series)│
                              └──────────────┘
```

- **ESP32** (`waterly/`): Firmware en C con ESP-IDF. Lee el sensor AS7265x por I2C, muestra datos en pantalla Nextion, envía/recibe comandos por MQTT.
- **Python API** (`waterly_server/backend/`): FastAPI que actúa como puente entre Mosquitto (MQTT) y ThingsBoard. Ejecuta el pipeline ML (`brain.py`).
- **ThingsBoard**: Dashboard IoT con botones RPC para calibrar, entrenar, analizar y cambiar modos.
- **InfluxDB**: Almacena series temporales de las mediciones.
- **Mosquitto**: Broker MQTT para comunicación ESP32 ↔ API.
- **mDNS publisher**: Publica la IP del servidor para que el ESP32 lo descubra automáticamente.

## Requisitos

- **ESP32** con sensor AS7265x y pantalla Nextion
- **ESP-IDF v5.5.1** para compilar firmware
- **Docker** + Docker Compose para el backend
- **ThingsBoard** (incluido en docker-compose)

## Compilar y Flashear Firmware

```bash
# Activar ESP-IDF (una vez por sesión)
. ~/esp/v5.5.1/esp-idf/export.sh

# Desde el directorio waterly/
cd waterly
./compile.sh    # Compilar
./flash.sh      # Flashear (espera 5s antes)
./monitor.sh    # Ver logs serie
./clean.sh      # Limpiar build/
```

**Orden**: compile → flash → monitor. Tras flashear, esperar ~5s antes de monitorear.

## Iniciar Backend

```bash
# Desde la raíz del proyecto
./start.sh      # docker compose up -d
./stop.sh       # docker compose down
./rebuild.sh    # Reconstruir y reiniciar
```

Los servicios arrancan en este orden: Mosquitto → ThingsBoard (espera healthy) → Python API.

## Puertos

| Servicio    | Puerto | Descripción           |
|-------------|--------|-----------------------|
| Mosquitto   | 1883   | Broker MQTT           |
| InfluxDB    | 8086   | Base de datos         |
| ThingsBoard | 8080   | Dashboard web         |
| FastAPI     | 8000   | API REST + OTA        |

## Usar el Sistema

### Configuración del ESP32

Todos los parámetros configurables (WiFi, MQTT, sensor, BLE, OTA) se almacenan en NVS.

**Desde ThingsBoard** (widget de configuración):
1. Importar el widget bundle: Widget Library → **+** → **Import** → `dashboards/waterly_custom_widgets.json`
2. Importar el dashboard: Dashboards → **+** → **Import** → `dashboards/waterly_config_dashboard.json`
3. Abrir el dashboard → Edit → pestaña **Data** → añadir dispositivo `Waterly_ESP32`
4. Rellenar solo los campos que quieras cambiar → **Guardar y Reiniciar**

**Vía MQTT** (manual):
```bash
mosquitto_pub -h <IP> -t "waterly/comandos" \
  -m '{"config":{"wifi_ssid":"MiRed","wifi_pass":"1234","sensor_gain":3}}'
```
Claves soportadas: `wifi_ssid`, `wifi_pass`, `mqtt_broker`, `mqtt_topic_cmd`, `mqtt_topic_dat`, `sensor_gain`, `sensor_integration`, `sensor_led_current`, `ble_pop`, `ota_url`, `factory_reset`.

**Factory Reset**: Borra toda la configuración y vuelve a estado de fábrica (inicia BLE provisioning).
```bash
mosquitto_pub -h <IP> -t "waterly/comandos" -m '{"config":{"factory_reset":true}}'
```

### Actualización OTA de Firmware

El sistema soporta actualizaciones Over-The-Air directamente desde ThingsBoard.

**Desde el widget "Firmware Upload"**:
1. Compilar el firmware: `cd waterly && ./compile.sh`
2. El binario se genera en `waterly/build/waterly.bin`
3. Abrir el dashboard → widget "Firmware Upload"
4. Seleccionar el archivo `.bin`
5. Introducir un número de versión (debe ser mayor que la versión actual del ESP32, que empieza en 1)
6. Pulsar **Subir y Actualizar**

**Flujo automático**:
- El widget sube el `.bin` al backend FastAPI (`POST /api/firmware/upload`)
- El backend guarda el firmware y genera `version.json`
- El backend envía automáticamente un comando OTA al ESP32 via MQTT
- El ESP32 descarga el firmware via HTTP (`http://waterly.local:8000/firmware/waterly.bin`)
- Se flashea en la partición OTA y reinicia

**Vía curl** (alternativa):
```bash
curl -F "file=@waterly/build/waterly.bin" -F "version=2" http://localhost:8000/api/firmware/upload
```

**Requisitos**:
- El ESP32 debe poder resolver `waterly.local` via mDNS (ya configurado por defecto)
- El backend FastAPI debe estar corriendo (puerto 8000)
- La URL OTA por defecto es `http://waterly.local:8000/firmware/version.json`
- Se puede cambiar via MQTT: `{"config": {"ota_url": "http://mi-servidor/version.json"}}`

### Desde ThingsBoard (operación)

1. **Calibrar**: Colocar agua limpia → botón `calibrate` en dashboard.
2. **Entrenar**: Preparar muestra con nitratos conocidos → botón `startTraining` con etiqueta (ej: "10.0").
3. **Entrenar modelo**: Cuando tengas suficientes muestras → botón `trainModel`.
4. **Predecir**: Colocar muestra desconocida → botón `startAnalysis`.

### Desde la Pantalla Nextion

Botones disponibles: IDLE, SCAN (medida única), TRAIN (continuo), OTA, SLEEP, RESET, WIFI CHECK.

### Modos ML

- **DEVIATION**: Distancia euclidiana desde la calibración en bandas UV/IR. Mínimo 1 muestra.
- **PLSR**: Regresión por mínimos cuadrados parciales. Mínimo 3 muestras.

## Ejecutar Tests

```bash
./run_test.sh   # Ejecuta test_brain.py dentro del contenedor
```

## Estructura del Proyecto

```
├── waterly/                    # Firmware ESP32 (ESP-IDF)
│   ├── main/                   # Punto de entrada
│   ├── components/
│   │   ├── app_controller/     # Máquina de estados principal
│   │   ├── as7265x/            # Driver del sensor espectral
│   │   ├── config_manager/     # Gestión de configuración NVS
│   │   ├── mqtt_app/           # Cliente MQTT + config OTA
│   │   ├── nextion/            # Driver pantalla Nextion
│   │   ├── wifi/               # Conexión WiFi + BLE provisioning
│   │   ├── ota/                # Actualizaciones OTA (HTTP manual)
│   │   └── ssd1306/            # Driver OLED (opcional)
│   └── compile.sh / flash.sh / monitor.sh
│
├── waterly_server/             # Backend Python
│   ├── backend/
│   │   ├── main.py             # API + puente MQTT + state machine + OTA upload
│   │   ├── brain.py            # Pipeline ML (calibración, entrenamiento, predicción)
│   │   ├── test_brain.py       # Tests unitarios
│   │   ├── .env.example        # Plantilla de variables de entorno
│   │   └── Dockerfile
│   ├── firmware/               # Firmware binario subido via OTA
│   ├── mosquitto/              # Config broker MQTT
│   ├── mdns_publisher/         # Servicio mDNS para descubrimiento
│   └── docker-compose.yml
│
├── dashboards/                 # Dashboards y widgets de ThingsBoard
│   ├── nuevo_panel.json        # Dashboard principal (RPC buttons)
│   └── widget/
│       ├── config_panel/       # Widget de configuración del ESP32
│       │   ├── index.html
│       │   ├── style.css
│       │   └── script.js
│       ├── firmware_upload/    # Widget de actualización OTA
│       │   ├── index.html
│       │   ├── style.css
│       │   └── script.js
│       └── configure_esp_widget.json
├── nextion_binary/             # Firmware pantalla Nextion (.tft)
├── start.sh / stop.sh / rebuild.sh / run_test.sh
└── README.md
```

## Flujo de Comunicación

```
ThingsBoard RPC → Python API (main.py) → Mosquitto → ESP32 → AS7265x
ESP32 → MQTT (waterly/datos) → Python API → InfluxDB + ThingsBoard telemetry
```
