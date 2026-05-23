# Waterly Widgets

Widgets personalizados para ThingsBoard. Cada widget tiene su propia carpeta con `index.html`, `style.css` y `script.js`.

## Estructura

```
widget/
├── config_panel/          # Panel de configuracion del ESP32
│   ├── index.html         # Estructura HTML
│   ├── style.css          # Estilos CSS
│   └── script.js          # Logica JavaScript (ThingsBoard onInit)
├── firmware_upload/       # Panel de actualizacion OTA
│   ├── index.html         # Estructura HTML
│   ├── style.css          # Estilos CSS
│   └── script.js          # Logica JavaScript (ThingsBoard onInit)
└── configure_esp_widget.json  # Backup del widget bundle
```

## Como crear un widget en ThingsBoard

1. Ve a **Widget Bundles** → crea uno nuevo o usa uno existente
2. Crea un widget tipo **HTML/CSS/JS** (Static Widget)
3. Configura el widget:
   - **HTML**: Copia el contenido de `index.html`
   - **CSS**: Copia el contenido de `style.css`
   - **JS**: Copia el contenido de `script.js`
4. En la pestaña **Advanced**, asegurate de que:
   - **Self context** este habilitado (para acceder a `self.ctx`)
   - El widget tenga una **entidad** configurada (el dispositivo Waterly_ESP32)

**Nota para ThingsBoard 4.2.1.1+**: Los widgets corren dentro de un iframe sandboxed. Las peticiones HTTP directas (`fetch`, `XMLHttpRequest`) a rutas relativas van al servidor de ThingsBoard, no al backend. Usa URLs absolutas para endpoints externos (ej: `http://localhost:8000/api/firmware/upload`).

## Widget: Config Panel

Permite configurar parametros del ESP32 via ThingsBoard RPC:
- WiFi (SSID, password)
- MQTT (broker IP, topics)
- Sensor (gain, integration time, LED current)
- BLE (POP password)
- OTA URL (url del version.json)
- Factory Reset

**RPC methods usados:** `saveConfig`, `factoryReset`

## Widget: Firmware Upload

Permite subir un firmware `.bin` y triggerar una actualizacion OTA:
1. Selecciona el archivo `.bin`
2. Introduce el numero de version (debe ser > version actual del ESP32)
3. Pulsa "Subir y Actualizar"

**Flujo:**
- El widget sube el `.bin` a `POST http://localhost:8000/api/firmware/upload` (FastAPI)
- El backend guarda el archivo y genera `version.json`
- El widget envia RPC `updateFirmware` al ESP32
- El ESP32 descarga el firmware via HTTP y se actualiza

**RPC methods usados:** `updateFirmware`

**Requisitos:**
- El backend FastAPI debe estar corriendo (puerto 8000)
- El ESP32 debe poder resolver `waterly.local` via mDNS
- CORS habilitado en FastAPI (ya configurado por defecto)
