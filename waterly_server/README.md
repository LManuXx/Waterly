# Waterly Server

Backend Python para el sistema Waterly — puente MQTT, pipeline ML y servidor OTA.

## Servicios

| Servicio | Puerto | Descripción |
|----------|--------|-------------|
| Mosquitto | 1883 | Broker MQTT |
| InfluxDB | 8086 | Base de datos time-series |
| ThingsBoard | 8080 | Dashboard IoT |
| FastAPI | 8000 | API REST + OTA firmware server |

## Estructura

```
waterly_server/
├── backend/
│   ├── main.py             # API + puente MQTT + state machine + OTA upload
│   ├── brain.py            # Pipeline ML (calibración, entrenamiento, predicción)
│   ├── test_brain.py       # Tests unitarios
│   ├── .env.example        # Plantilla de variables de entorno
│   ├── requirements.txt
│   └── Dockerfile
├── firmware/               # Firmware binario subido via OTA (gitignored)
├── mosquitto/              # Config broker MQTT
├── mdns_publisher/         # Servicio mDNS para descubrimiento
└── docker-compose.yml
```

## Endpoints FastAPI

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `GET` | `/api/firmware/version` | Info del firmware actual |
| `POST` | `/api/firmware/upload` | Subir firmware `.bin` (form: `file` + `version`) |
| `GET` | `/firmware/waterly.bin` | Descargar firmware (StaticFiles) |

## RPC Methods (ThingsBoard → ESP32)

| Method | Params | Descripción |
|--------|--------|-------------|
| `setIdle` | - | Forzar estado IDLE |
| `startFreeMeasure` | - | Iniciar monitorización continua |
| `calibrate` | número de muestras | Iniciar calibración |
| `startTraining` | `{label, samples}` | Iniciar entrenamiento |
| `startAnalysis` | número de muestras | Iniciar análisis/predicción |
| `trainModel` | - | Entrenar modelo ML |
| `resetModel` | - | Borrar modelo entrenado |
| `setModeDeviation` | - | Cambiar a modo DEVIATION |
| `setModePLSR` | - | Cambiar a modo PLSR |
| `getModelInfo` | - | Obtener info del modelo |
| `saveConfig` | `{key: value}` | Enviar config al ESP32 via MQTT |
| `factoryReset` | - | Factory reset del ESP32 |
| `updateFirmware` | - | Trigger OTA update en el ESP32 |

## Comandos

```bash
./start.sh      # docker compose up -d
./stop.sh       # docker compose down
./rebuild.sh    # docker compose down && docker compose up -d --build
./run_test.sh   # Ejecutar tests
```
