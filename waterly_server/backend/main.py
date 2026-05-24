import paho.mqtt.client as mqtt
import json
import time
import threading
import os
import requests
import shutil
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import ASYNCHRONOUS
from brain import SpectralBrain
from enum import Enum

class SystemState(Enum):
    IDLE = "IDLE"
    CALIBRATION = "CALIBRATION"
    FREE_MEASURE = "FREE_MEASURE"
    TRAINING = "TRAINING"
    ANALYSIS = "ANALYSIS"

# --- CONFIGURACION ---
TB_ACCESS_TOKEN = os.environ.get("TB_ACCESS_TOKEN", "x35f744geqt5lgsnlwsk")
TB_HOST = os.environ.get("TB_HOST", "thingsboard")
MOSQUITTO_HOST = os.environ.get("MOSQUITTO_HOST", "mosquitto")

# Credenciales de Admin (Tenant)
TB_ADMIN_USER = os.environ.get("TB_ADMIN_USER", "tenant@thingsboard.org")
TB_ADMIN_PASS = os.environ.get("TB_ADMIN_PASS", "tenant")

# --- CONFIGURACION INFLUXDB ---
INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "admin")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "waterly_org")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "sensor_data")

# --- MEMORIA DE LA API ---
CURRENT_STATE = SystemState.IDLE
BURST_CONTEXT = {
    "target_count": 0,
    "current_buffer": [],
    "label": "Unknown"
}
BURST_TIMER = None
BURST_TIMEOUT_S = 20

FIRMWARE_DIR = "/app/firmware"
FIRMWARE_BIN = os.path.join(FIRMWARE_DIR, "waterly.bin")
FIRMWARE_VERSION_JSON = os.path.join(FIRMWARE_DIR, "version.json")

brain = SpectralBrain()

try:
    client_influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client_influx.write_api(write_options=ASYNCHRONOUS)
    print("Conexion con InfluxDB lista (modo asíncrono)")
except Exception as e:
    print(f"Fallo al conectar con InfluxDB: {e}")

# Clientes MQTT
client_mosquitto = mqtt.Client(client_id="Bridge_To_Mosquitto")
client_tb = mqtt.Client(client_id="Bridge_To_ThingsBoard")
client_tb.username_pw_set(TB_ACCESS_TOKEN)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def clean_and_validate_data(raw_json):
    try:
        data = json.loads(raw_json)
        clean_data = {}

        for key, val in data.items():
            if isinstance(val, (int, float)):
                if -100 <= val <= 100000:
                    clean_data[key] = val

        if len(clean_data) > 0:
            return clean_data

        return None

    except json.JSONDecodeError:
        print("JSON invalido recibido")
        return None

def save_to_influx(data, is_absorbance=False):
    try:
        if not data: return
        measurement_name = "water_quality_abs" if is_absorbance else "water_quality_raw"
        p = Point(measurement_name) \
            .tag("device", "ESP32_01") \
            .tag("state", CURRENT_STATE.value)

        for key, val in data.items():
            if key in ["pc1", "pc2", "pred_deviation"] or "_nm" in key:
                p.field(key, float(val))
        
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)
        
    except Exception as e:
        print(f"Error InfluxDB: {e}")

def autoconfig_thingsboard():
    print("[AUTO-CONFIG] Verificando dispositivo en ThingsBoard...")
    base_url = f"http://{TB_HOST}:9090"
    try:
        resp = requests.post(f"{base_url}/api/auth/login", json={"username": TB_ADMIN_USER, "password": TB_ADMIN_PASS})
        if resp.status_code != 200: return False 
        headers = {"X-Authorization": f"Bearer {resp.json()['token']}"}
        
        device_name = "Waterly_ESP32"
        check_url = f"{base_url}/api/tenant/devices?deviceName={device_name}"
        resp = requests.get(check_url, headers=headers)
        
        device_id = None
        if resp.status_code == 200:
            device_id = resp.json()["id"]["id"]
            print(f"Dispositivo '{device_name}' encontrado (ID: {device_id}).")
        else:
            print(f"Creando dispositivo '{device_name}'...")
            new_device = {"name": device_name, "type": "ESP32_Sensor"}
            resp = requests.post(f"{base_url}/api/device", json=new_device, headers=headers)
            if resp.status_code == 200:
                device_id = resp.json()["id"]["id"]
                print(f"Dispositivo creado con exito.")
            else:
                return True 

        if device_id:
            get_url = f"{base_url}/api/device/{device_id}/credentials"
            save_url = f"{base_url}/api/device/credentials"
            resp = requests.get(get_url, headers=headers)
            if resp.status_code == 200:
                creds_data = resp.json()
                current_token = creds_data.get("credentialsId")
                if current_token != TB_ACCESS_TOKEN:
                    print(f"Actualizando token a '{TB_ACCESS_TOKEN}'...")
                    payload = {
                        "id": creds_data.get("id"), 
                        "createdTime": creds_data.get("createdTime"),
                        "deviceId": creds_data.get("deviceId"),
                        "credentialsType": "ACCESS_TOKEN",
                        "credentialsId": TB_ACCESS_TOKEN,
                        "credentialsValue": None
                    }
                    save_resp = requests.post(save_url, json=payload, headers=headers)
            return True
    except Exception as e:
        return False
    return False

def calculate_average_spectrum(buffer):
    if not buffer: return None
    first_sample = buffer[0]
    averaged_data = {}
    for key in first_sample.keys():
        if isinstance(first_sample[key], (int, float)):
            total = sum(d.get(key, 0) for d in buffer)
            averaged_data[key] = round(total / len(buffer), 2)
    return averaged_data

def reset_burst_timer():
    global BURST_TIMER
    if BURST_TIMER is not None:
        BURST_TIMER.cancel()
    BURST_TIMER = threading.Timer(BURST_TIMEOUT_S, burst_timeout_handler)
    BURST_TIMER.daemon = True
    BURST_TIMER.start()

def burst_timeout_handler():
    global CURRENT_STATE, BURST_CONTEXT
    if CURRENT_STATE in [SystemState.CALIBRATION, SystemState.TRAINING, SystemState.ANALYSIS]:
        print(f"[BURST_TIMEOUT] Auto-reset from {CURRENT_STATE.value} (no data from ESP32)")
        CURRENT_STATE = SystemState.IDLE
        BURST_CONTEXT = {"target_count": 0, "current_buffer": [], "label": "Unknown"}
        client_mosquitto.publish("waterly/comandos", json.dumps({"mode": "idle"}), retain=True)

def send_telemetry(raw_data, abs_data=None, is_calibrated=False, prediction_status=None, prediction_value=None, is_progress=False):
    final_package = {}
    final_package["system_state"] = CURRENT_STATE.value
    final_package["calibrated"] = is_calibrated

    if prediction_status:
        final_package["prediction_status"] = str(prediction_status)
    if prediction_value is not None:
        final_package["pred_deviation"] = prediction_value

    if not is_progress:
        for k, v in raw_data.items():
            if isinstance(v, (int, float)):
                final_package[f"raw_{k}"] = v
        if abs_data:
            final_package.update(abs_data)
        save_to_influx(final_package, is_absorbance=(abs_data is not None))
    else:
        if prediction_status:
            final_package["prediction_status"] = f"[PROGRESS] {prediction_status}"

    client_tb.publish("v1/devices/me/telemetry", json.dumps(final_package))


def on_mosquitto_message(client, userdata, msg):
    global CURRENT_STATE, BURST_CONTEXT, BURST_TIMER

    try:
        raw_payload = msg.payload.decode()
        raw_data = clean_and_validate_data(raw_payload)
        if not raw_data:
            return

        abs_data, is_calibrated, snv_data = brain.get_absorbance(raw_data)

        if CURRENT_STATE == SystemState.IDLE:
            send_telemetry(raw_data, abs_data, is_calibrated)
            return

        elif CURRENT_STATE == SystemState.FREE_MEASURE:
            prediction_status = "Monitorizando"
            prediction_value = None
            if is_calibrated and brain.is_trained:
                prediction_value = brain.predict(snv_data)
            elif not is_calibrated:
                prediction_status = "Falta Calibrar"
            elif not brain.is_trained:
                prediction_status = "Falta Entrenar"

            send_telemetry(raw_data, abs_data, is_calibrated, prediction_status, prediction_value)

        elif CURRENT_STATE in [SystemState.CALIBRATION, SystemState.TRAINING, SystemState.ANALYSIS]:
            if len(BURST_CONTEXT["current_buffer"]) >= BURST_CONTEXT["target_count"]:
                return

            BURST_CONTEXT["current_buffer"].append(raw_data)
            count = len(BURST_CONTEXT["current_buffer"])
            target = BURST_CONTEXT["target_count"]
            reset_burst_timer()

            print(f"[{CURRENT_STATE.value}] Recibiendo muestra {count}/{target}...")
            client_tb.publish("v1/devices/me/telemetry", json.dumps({
                "prediction_status": f"{CURRENT_STATE.value} ({count}/{target})",
                "system_state": CURRENT_STATE.value
            }))

            if count < target:
                client_mosquitto.publish("waterly/comandos", json.dumps({"mode": "single"}), qos=1, retain=False)
            else:
                if BURST_TIMER:
                    BURST_TIMER.cancel()
                    BURST_TIMER = None

                print(f"[{CURRENT_STATE.value}] Burst completado. Procesando...")

                if CURRENT_STATE == SystemState.CALIBRATION:
                    success = brain.calibrate(BURST_CONTEXT["current_buffer"])
                    status = "Calibracion Exitosa" if success else "Error en Calibracion"
                    print(f"--> {status}")
                    send_telemetry(raw_data, is_calibrated=is_calibrated, prediction_status=status)

                elif CURRENT_STATE == SystemState.TRAINING:
                    if is_calibrated:
                        avg_raw = calculate_average_spectrum(BURST_CONTEXT["current_buffer"])
                        _, _, avg_snv = brain.get_absorbance(avg_raw)

                        if avg_snv is None:
                            print("--> Error: No se pudo calcular absorbancia del promedio.")
                            send_telemetry(avg_raw, abs_data, is_calibrated, prediction_status="Error: Absorbancia fallida")
                        else:
                            try:
                                num_val = float(BURST_CONTEXT["label"])
                            except ValueError:
                                num_val = 0.0
                                print("--> Aviso: Etiqueta no numérica. Usando 0.0 (válido para modo DEVIATION).")

                            brain.add_training_sample(avg_snv, num_val)
                            print(f"--> Muestra {num_val} guardada. Dataset total: {len(brain.dataset_X)}")
                            brain.save_brain()
                            send_telemetry(avg_raw, abs_data, is_calibrated,
                                prediction_status=f"Muestra Guardada ({len(brain.dataset_X)} en memoria)")
                    else:
                        print("--> Error: Falta calibrar.")
                        send_telemetry(raw_data, abs_data, is_calibrated, prediction_status="Error: Falta Calibrar")

                elif CURRENT_STATE == SystemState.ANALYSIS:
                    if is_calibrated and brain.is_trained:
                        avg_raw = calculate_average_spectrum(BURST_CONTEXT["current_buffer"])
                        _, _, avg_snv = brain.get_absorbance(avg_raw)
                        pred = brain.predict(avg_snv)
                        print(f"--> Analisis: {pred}")
                        send_telemetry(avg_raw, abs_data, is_calibrated,
                            prediction_status="Analisis Finalizado", prediction_value=pred)
                    else:
                        print("--> Error: Modelo no listo.")
                        send_telemetry(raw_data, abs_data, is_calibrated, prediction_status="Falta modelo o calibracion")

                CURRENT_STATE = SystemState.IDLE
                BURST_CONTEXT["current_buffer"] = []
                client_mosquitto.publish("waterly/comandos", json.dumps({"mode": "idle"}), retain=True)

    except Exception as e:
        print(f"Error en bucle principal: {e}")

# --- PUENTE MQTT (NUBE -> ESP32) ---
def on_tb_message(client, userdata, msg):
    global CURRENT_STATE, BURST_CONTEXT, BURST_TIMER
    
    try:
        data = json.loads(msg.payload)
        method = data.get("method")
        params = data.get("params")
        rpc_id = msg.topic.split("/")[-1] if "/" in msg.topic else "0"

        esp_payload = None
        should_retain = False

        print(f"[RPC] Recibido: {method} | params: {params} | id: {rpc_id}")

        if method == "setIdle":
            CURRENT_STATE = SystemState.IDLE
            BURST_CONTEXT = {"target_count": 0, "current_buffer": [], "label": "Unknown"}
            if BURST_TIMER is not None:
                BURST_TIMER.cancel()
                BURST_TIMER = None
            esp_payload = {"mode": "idle"}
            should_retain = True
            client_tb.publish("v1/devices/me/telemetry", json.dumps({"prediction_status": "Forzado a Reposo"}))
            
        elif method == "startFreeMeasure":
            CURRENT_STATE = SystemState.FREE_MEASURE
            esp_payload = {"mode": "training"}
            should_retain = True

        elif method == "calibrate":
            samples = int(params) if params else 5
            CURRENT_STATE = SystemState.CALIBRATION
            BURST_CONTEXT = {"target_count": samples, "current_buffer": [], "label": "Unknown"}
            esp_payload = {"mode": "single"}
            should_retain = True

        elif method == "startTraining":
            label = "0.0"
            samples = 5
            if isinstance(params, dict):
                label = str(params.get("label", "0.0"))
                samples = int(params.get("samples", 5))
            else:
                label = str(params)

            CURRENT_STATE = SystemState.TRAINING
            BURST_CONTEXT = {"target_count": samples, "current_buffer": [], "label": label}
            esp_payload = {"mode": "single"}
            should_retain = True

        elif method == "startAnalysis":
            if not brain.is_trained:
                client_tb.publish("v1/devices/me/telemetry", json.dumps({
                    "prediction_status": "Error: Modelo no entrenado. Primero haz trainModel.",
                    "pred_deviation": 0.0
                }))
                print("[CMD] startAnalysis bloqueado: modelo no entrenado.")
                return
            if brain.baseline is None:
                client_tb.publish("v1/devices/me/telemetry", json.dumps({
                    "prediction_status": "Error: Falta calibrar (baseline no establecida).",
                    "pred_deviation": 0.0
                }))
                print("[CMD] startAnalysis bloqueado: falta calibrar.")
                return
            samples = int(params) if params else 5
            CURRENT_STATE = SystemState.ANALYSIS
            BURST_CONTEXT = {"target_count": samples, "current_buffer": [], "label": "Unknown"}
            esp_payload = {"mode": "single"}
            should_retain = True
            
        elif method == "trainModel":
            if CURRENT_STATE in [SystemState.CALIBRATION, SystemState.TRAINING, SystemState.ANALYSIS]:
                msg = f"Burst activo ({CURRENT_STATE.value}). Pulsa IDLE y espera."
                client_tb.publish("v1/devices/me/telemetry", json.dumps({"prediction_status": msg}))
                print(f"[CMD] Burst en curso ({CURRENT_STATE.value}), no se puede entrenar ahora.")
                return
            
            # Entrenar en un hilo separado para no bloquear la cola MQTT
            def _train():
                n_samples = len(brain.dataset_X)
                print(f"[CMD] Entrenando modelo ({brain.model_type}) con {n_samples} muestras...")
                client_tb.publish("v1/devices/me/telemetry", json.dumps({
                    "prediction_status": f"Entrenando modelo {brain.model_type} con {n_samples} muestras..."
                }))
                success = brain.train_model()
                if success:
                    metrics = brain.get_model_info()
                    if brain.model_type == "DEVIATION":
                        summary = f"ENTRENAMIENTO COMPLETADO | DEVIATION | {metrics.get('n_samples','?')} muestras | {metrics.get('n_features','?')} bandas"
                    else:
                        r2 = metrics.get("r2_train", 0)
                        rmsecv = metrics.get("rmsecv", 0)
                        n_comp = metrics.get("n_components", 0)
                        summary = f"ENTRENAMIENTO COMPLETADO | PLSR | {n_samples} muestras | {n_comp} comp | R2={r2:.2f} | RMSECV={rmsecv:.1f} mg/L"
                    client_tb.publish("v1/devices/me/telemetry", json.dumps({
                        "prediction_status": summary,
                        "pred_deviation": metrics.get("rmsecv", 0),
                        "model_ready": True
                    }))
                else:
                    client_tb.publish("v1/devices/me/telemetry", json.dumps({
                        "prediction_status": f"ERROR ENTRENAMIENTO | Muestras: {n_samples} | Mira logs del API"
                    }))
                print(f"[CMD] ML Resultado: {'OK' if success else 'Error'}")
            
            threading.Thread(target=_train, daemon=True).start()
            return

        elif method == "resetModel":
            brain.reset_model()
            client_tb.publish("v1/devices/me/telemetry", json.dumps({"prediction_status": "Modelo Borrado", "pred_deviation": 0.0}))
            return

        elif method == "setModeDeviation":
            brain.model_type = "DEVIATION"
            print("[CMD] Modo cambiado a: DEVIATION")
            client_tb.publish("v1/devices/me/telemetry", json.dumps({"prediction_status": "Modo: DEVIATION"}))
            return

        elif method == "setModePLSR":
            brain.model_type = "PLSR"
            print("[CMD] Modo cambiado a: PLSR")
            client_tb.publish("v1/devices/me/telemetry", json.dumps({"prediction_status": "Modo: PLSR"}))
            return

        elif method == "getModelInfo":
            info = brain.get_model_info()
            client_tb.publish("v1/devices/me/telemetry", json.dumps({"prediction_status": json.dumps(info)}))
            return

        elif method == "saveConfig":
            # Configuracion OTA del ESP32 desde ThingsBoard
            if not params:
                client_tb.publish(f"v1/devices/me/rpc/response/{rpc_id}", json.dumps({"result": "error: no params"}))
                print("[CONFIG] saveConfig sin parametros")
                return
            
            config_payload = {"config": params}
            client_mosquitto.publish("waterly/comandos", json.dumps(config_payload), qos=1, retain=False)
            
            # Responder inmediatamente a ThingsBoard para evitar timeout
            client_tb.publish(f"v1/devices/me/rpc/response/{rpc_id}", json.dumps({"result": "Config enviada al ESP32. Reiniciando..."}))
            client_tb.publish("v1/devices/me/telemetry", json.dumps({
                "prediction_status": f"Configuracion enviada al ESP32. Reiniciando..."
            }))
            print(f"[CONFIG] saveConfig enviado al ESP32: {params}")
            return

        elif method == "factoryReset":
            reset_payload = {"config": {"factory_reset": True}}
            client_mosquitto.publish("waterly/comandos", json.dumps(reset_payload), qos=1, retain=False)
            client_tb.publish(f"v1/devices/me/rpc/response/{rpc_id}", json.dumps({"result": "Factory reset enviado"}))
            client_tb.publish("v1/devices/me/telemetry", json.dumps({
                "prediction_status": "Factory Reset enviado al ESP32"
            }))
            print("[CONFIG] Factory Reset enviado al ESP32")
            return

        elif method == "updateFirmware":
            if os.path.exists(FIRMWARE_BIN) and os.path.exists(FIRMWARE_VERSION_JSON):
                with open(FIRMWARE_VERSION_JSON, "r") as f:
                    ver_data = json.load(f)
                client_mosquitto.publish("waterly/comandos", json.dumps({"update": True}), qos=1, retain=False)
                client_tb.publish(f"v1/devices/me/rpc/response/{rpc_id}", json.dumps({"result": f"Update iniciado (v{ver_data['version']})"}))
                client_tb.publish("v1/devices/me/telemetry", json.dumps({
                    "prediction_status": f"OTA iniciada - version {ver_data['version']}"
                }))
                print(f"[OTA] updateFirmware RPC -> ESP32 (v{ver_data['version']})")
            else:
                client_tb.publish(f"v1/devices/me/rpc/response/{rpc_id}", json.dumps({"result": "error: no firmware uploaded"}))
                client_tb.publish("v1/devices/me/telemetry", json.dumps({
                    "prediction_status": "Error: No hay firmware subido"
                }))
                print("[OTA] updateFirmware RPC -> sin firmware disponible")
            return

        if esp_payload:
            client_mosquitto.publish("waterly/comandos", json.dumps(esp_payload), retain=should_retain)
            client_tb.publish(f"v1/devices/me/rpc/response/{rpc_id}", json.dumps({"result": "ok"}))
            print(f"[RPC] Forwarded to ESP32 via Mosquitto: {esp_payload} (retain={should_retain})")

    except Exception as e:
        print(f"Error RPC: {e}")


@app.on_event("startup")
def start_bridge():
    print(">>> INICIANDO WATERLY API - STATE MACHINE v3.0 <<<")

    tb_ready = False
    for i in range(30):
        if autoconfig_thingsboard():
            print("ThingsBoard configurado.")
            tb_ready = True
            break
        time.sleep(5)

    def on_mosquitto_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            print("[MQTT] Mosquitto conectado")
            client_mosquitto.subscribe("waterly/datos")
        else:
            print(f"[MQTT] Error conexión Mosquitto: {rc}")

    def on_mosquitto_disconnect(client, userdata, rc):
        print(f"[MQTT] Mosquitto desconectado (rc={rc}), paho-mqtt intentara reconnect...")

    def on_tb_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            print("[MQTT] ThingsBoard conectado")
            client_tb.subscribe("v1/devices/me/rpc/request/+")
        else:
            print(f"[MQTT] Error conexión ThingsBoard: {rc}")

    def on_tb_disconnect(client, userdata, rc):
        print(f"[MQTT] ThingsBoard desconectado (rc={rc}), paho-mqtt intentara reconnect...")

    client_mosquitto.on_connect = on_mosquitto_connect
    client_mosquitto.on_disconnect = on_mosquitto_disconnect
    client_mosquitto.on_message = on_mosquitto_message
    client_tb.on_connect = on_tb_connect
    client_tb.on_disconnect = on_tb_disconnect
    client_tb.on_message = on_tb_message

    try:
        client_mosquitto.connect(MOSQUITTO_HOST, 1883, 60)
        client_mosquitto.loop_start()

        client_tb.connect(TB_HOST, 1883, 60)
        client_tb.loop_start()
    except Exception as e:
        print(f"Error MQTT: {e}")

@app.get("/")
def read_root(): return {"status": "Online", "state": CURRENT_STATE.value}

@app.get("/api/firmware/version")
def get_firmware_version():
    if os.path.exists(FIRMWARE_VERSION_JSON):
        with open(FIRMWARE_VERSION_JSON, "r") as f:
            return json.load(f)
    return JSONResponse(status_code=404, content={"error": "No firmware uploaded"})

@app.post("/api/firmware/upload")
async def upload_firmware(file: UploadFile = File(...), version: int = Form(...)):
    if not file.filename or not file.filename.endswith(".bin"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .bin")
    
    if version < 1:
        raise HTTPException(status_code=400, detail="La version debe ser >= 1")
    
    os.makedirs(FIRMWARE_DIR, exist_ok=True)
    
    with open(FIRMWARE_BIN, "wb") as f:
        shutil.copyfileobj(file.file, f)
    
    file_size = os.path.getsize(FIRMWARE_BIN)
    
    version_data = {
        "version": version,
        "url": "http://waterly.local:8000/firmware/waterly.bin"
    }
    with open(FIRMWARE_VERSION_JSON, "w") as f:
        json.dump(version_data, f)
    
    print(f"[OTA] Firmware v{version} subido ({file_size} bytes)")
    
    def trigger_ota():
        time.sleep(1)
        client_mosquitto.publish("waterly/comandos", json.dumps({"update": True}), qos=1, retain=False)
        print("[OTA] Comando update enviado al ESP32")
    
    threading.Thread(target=trigger_ota, daemon=True).start()
    
    return {"status": "ok", "version": version, "size": file_size}

app.mount("/firmware", StaticFiles(directory=FIRMWARE_DIR), name="firmware")

MODEL_FILE_PATH = "/app/data/waterly_model.pkl"

@app.get("/api/model/info")
def get_model_info():
    info = brain.get_model_info()
    info["is_trained"] = brain.is_trained
    info["model_type"] = brain.model_type
    info["has_baseline"] = brain.baseline is not None
    info["n_samples"] = len(brain.dataset_X)
    return info

@app.get("/api/model/download")
def download_model():
    if not os.path.exists(MODEL_FILE_PATH):
        raise HTTPException(status_code=404, detail="No hay modelo guardado. Entrena un modelo primero.")
    return FileResponse(
        MODEL_FILE_PATH,
        media_type="application/octet-stream",
        filename="waterly_model.pkl"
    )

@app.post("/api/model/upload")
async def upload_model(file: UploadFile = File(...)):
    import joblib
    
    if not file.filename or not file.filename.endswith(".pkl"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .pkl")
    
    content = await file.read()
    
    try:
        import io
        state = joblib.load(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Archivo .pkl invalido o corrupto: {str(e)}")
    
    required_keys = ["baseline", "trained", "scaler", "model_type"]
    for key in required_keys:
        if key not in state:
            raise HTTPException(status_code=400, detail=f"Modelo invalido: falta la clave '{key}'")
    
    if state["trained"]:
        if state["scaler"] is None:
            raise HTTPException(status_code=400, detail="Modelo invalido: scaler es nulo en modelo entrenado")
        if state["model_type"] == "PLSR" and state.get("model") is None:
            raise HTTPException(status_code=400, detail="Modelo invalido: falta el modelo PLSR")
    
    with open(MODEL_FILE_PATH, "wb") as f:
        f.write(content)
    
    brain.load_brain()
    
    info = brain.get_model_info()
    print(f"[MODEL] Modelo cargado: tipo={state['model_type']}, trained={state['trained']}, baseline={'SI' if state['baseline'] is not None else 'NO'}")
    
    return {
        "status": "ok",
        "model_type": state["model_type"],
        "trained": state["trained"],
        "has_baseline": state["baseline"] is not None,
        "metrics": info if state["trained"] else {}
    }