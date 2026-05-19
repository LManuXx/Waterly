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
| FastAPI     | 8000   | API REST              |

## Usar el Sistema

### Desde ThingsBoard

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
│   │   ├── mqtt_app/           # Cliente MQTT
│   │   ├── nextion/            # Driver pantalla Nextion
│   │   ├── wifi/               # Conexión WiFi + BLE provisioning
│   │   ├── ota/                # Actualizaciones OTA
│   │   └── ssd1306/            # Driver OLED (opcional)
│   └── compile.sh / flash.sh / monitor.sh
│
├── waterly_server/             # Backend Python
│   ├── backend/
│   │   ├── main.py             # API + puente MQTT + state machine
│   │   ├── brain.py            # Pipeline ML (calibración, entrenamiento, predicción)
│   │   ├── test_brain.py       # Tests unitarios
│   │   └── Dockerfile
│   ├── mosquitto/              # Config broker MQTT
│   ├── mdns_publisher/         # Servicio mDNS para descubrimiento
│   └── docker-compose.yml
│
├── dashboards/                 # Dashboards de ThingsBoard (JSON)
├── nextion_binary/             # Firmware pantalla Nextion (.tft)
├── start.sh / stop.sh / rebuild.sh / run_test.sh
└── README.md
```

## Flujo de Comunicación

```
ThingsBoard RPC → Python API (main.py) → Mosquitto → ESP32 → AS7265x
ESP32 → MQTT (waterly/datos) → Python API → InfluxDB + ThingsBoard telemetry
```
