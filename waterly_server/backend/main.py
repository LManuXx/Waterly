import paho.mqtt.client as mqtt
import json
import time
import threading
import os
import requests
from fastapi import FastAPI
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
TB_ACCESS_TOKEN = "x35f744geqt5lgsnlwsk" 
TB_HOST = "thingsboard"
MOSQUITTO_HOST = "mosquitto"

# Credenciales de Admin (Tenant)
TB_ADMIN_USER = "tenant@thingsboard.org"
TB_ADMIN_PASS = "tenant"

# --- CONFIGURACION INFLUXDB ---
INFLUX_URL = "http://influxdb:8086"
INFLUX_TOKEN = "admin"
INFLUX_ORG = "waterly_org"
INFLUX_BUCKET = "sensor_data"

# --- MEMORIA DE LA API ---
CURRENT_STATE = SystemState.IDLE
BURST_CONTEXT = {
    "target_count": 0,
    "current_buffer": [],
    "label": "Unknown"
}

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

def clean_and_validate_data(raw_json):
    try:
        data = json.loads(raw_json)
        clean_data = {}
        
        for key, val in data.items():
            if isinstance(val, (int, float)):
                if -1000 <= val <= 100000: 
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

def send_telemetry(raw_data, abs_data=None, is_calibrated=False, prediction_status=None, prediction_value=None):
    final_package = {}
    
    # 1. Metadatos de estado actual
    final_package["system_state"] = CURRENT_STATE.value
    final_package["calibrated"] = is_calibrated
    
    if prediction_status:
        final_package["prediction_status"] = str(prediction_status)
    if prediction_value is not None:
        final_package["pred_deviation"] = prediction_value
        
    # 2. Datos Raw (Siempre se envían)
    for k, v in raw_data.items():
        if isinstance(v, (int, float)):
            final_package[f"raw_{k}"] = v

    # 3. Datos de Absorbancia (si están disponibles)
    if abs_data:
        final_package.update(abs_data)

    save_to_influx(final_package, is_absorbance=(abs_data is not None)) 
    client_tb.publish("v1/devices/me/telemetry", json.dumps(final_package))


def on_mosquitto_message(client, userdata, msg):
    global CURRENT_STATE, BURST_CONTEXT
    
    try:
        raw_payload = msg.payload.decode()
        raw_data = clean_and_validate_data(raw_payload)
        if not raw_data: return

        # FASE 1: SIEMPRE intentar calcular absorbancia
        abs_data, is_calibrated, snv_data = brain.get_absorbance(raw_data)
        
        # FASE 2: MÁQUINA DE ESTADOS
        
        # === ESTADO IDLE: Ignorar datos silenciosamente para no saturar ===
        if CURRENT_STATE == SystemState.IDLE:
            return

        # === ESTADO LECTURA LIBRE ===
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

        # === ESTADOS DE RÁFAGA (BURST) ===
        elif CURRENT_STATE in [SystemState.CALIBRATION, SystemState.TRAINING, SystemState.ANALYSIS]:
            # Ignorar datos viejos de la cola MQTT: si ya tenemos suficientes, no acumular más
            if len(BURST_CONTEXT["current_buffer"]) >= BURST_CONTEXT["target_count"]:
                return
                
            BURST_CONTEXT["current_buffer"].append(raw_data)
            count = len(BURST_CONTEXT["current_buffer"])
            target = BURST_CONTEXT["target_count"]
            
            print(f"[{CURRENT_STATE.value}] Recibiendo muestra {count}/{target}...")
            # Solo publicar progreso a TB (sin InfluxDB) para mantener velocidad
            client_tb.publish("v1/devices/me/telemetry", json.dumps({
                "prediction_status": f"{CURRENT_STATE.value} ({count}/{target})",
                "system_state": CURRENT_STATE.value
            }))
            
            # Pide la siguiente muestra o finaliza
            if count < target:
                # Timer de 0.3s: el ESP32 tarda ~0.1s en medir, 0.3s da margen de red
                def pedir_siguiente():
                    client_mosquitto.publish("waterly/comandos", json.dumps({"mode": "single"}), retain=False)
                threading.Timer(0.3, pedir_siguiente).start()
            else:
                print(f"[{CURRENT_STATE.value}] Burst completado. Procesando...")
                
                if CURRENT_STATE == SystemState.CALIBRATION:
                    success = brain.calibrate(BURST_CONTEXT["current_buffer"])
                    status = "Calibracion Exitosa" if success else "Error en Calibracion"
                    print(f"--> {status}")
                    send_telemetry(raw_data, prediction_status=status)
                    
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
                            send_telemetry(avg_raw, abs_data, is_calibrated, prediction_status=f"Muestra Guardada ({len(brain.dataset_X)} en memoria)")
                    else:
                        print("--> Error: Falta calibrar.")
                        send_telemetry(raw_data, prediction_status="Error: Falta Calibrar")

                elif CURRENT_STATE == SystemState.ANALYSIS:
                    if is_calibrated and brain.is_trained:
                        avg_raw = calculate_average_spectrum(BURST_CONTEXT["current_buffer"])
                        _, _, avg_snv = brain.get_absorbance(avg_raw)
                        pred = brain.predict(avg_snv)
                        print(f"--> Analisis: {pred}")
                        send_telemetry(avg_raw, abs_data, is_calibrated, prediction_status="Analisis Finalizado", prediction_value=pred)
                    else:
                        print("--> Error: Modelo no listo.")
                        send_telemetry(raw_data, abs_data, is_calibrated, prediction_status="Falta modelo o calibracion")
                
                # Volver a IDLE al terminar
                CURRENT_STATE = SystemState.IDLE
                BURST_CONTEXT["current_buffer"] = []
                client_mosquitto.publish("waterly/comandos", json.dumps({"mode": "idle"}), retain=True)

    except Exception as e:
        print(f"Error en bucle principal: {e}")

# --- PUENTE MQTT (NUBE -> ESP32) ---
def on_tb_message(client, userdata, msg):
    global CURRENT_STATE, BURST_CONTEXT
    
    try:
        data = json.loads(msg.payload)
        method = data.get("method")
        params = data.get("params")
        
        esp_payload = None
        should_retain = False 
        
        print(f"[RPC] Recibido: {method} | params: {params}")

        if method == "setIdle":
            CURRENT_STATE = SystemState.IDLE
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
            
        elif method == "startTraining":
            # Permite {"label": "5.0", "samples": 5} o solo "5.0"
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

        elif method == "startAnalysis":
            samples = int(params) if params else 5
            CURRENT_STATE = SystemState.ANALYSIS
            BURST_CONTEXT = {"target_count": samples, "current_buffer": [], "label": "Unknown"}
            esp_payload = {"mode": "single"}
            
        elif method == "trainModel":
            # Esperar a que termine cualquier burst en curso
            if CURRENT_STATE in [SystemState.CALIBRATION, SystemState.TRAINING, SystemState.ANALYSIS]:
                client_tb.publish("v1/devices/me/telemetry", json.dumps({"prediction_status": "Esperando fin de burst..."}))
                print("[CMD] Esperando a que termine el burst en curso...")
                for _ in range(60):  # max 30 segundos
                    time.sleep(0.5)
                    if CURRENT_STATE == SystemState.IDLE:
                        break
            
            n_samples = len(brain.dataset_X)
            print(f"[CMD] Entrenando modelo ({brain.model_type}) con {n_samples} muestras...")
            success = brain.train_model()
            if success:
                status_msg = f"Modelo Entrenado OK ({n_samples} muestras)"
            else:
                status_msg = f"Error entrenando. Muestras en memoria: {n_samples}"
            print(f"[CMD] ML Resultado: {status_msg}")
            client_tb.publish("v1/devices/me/telemetry", json.dumps({"prediction_status": status_msg}))
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

        if esp_payload:
            client_mosquitto.publish("waterly/comandos", json.dumps(esp_payload), retain=should_retain)
            
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

    try:
        client_mosquitto.connect(MOSQUITTO_HOST, 1883, 60)
        client_mosquitto.subscribe("waterly/datos")
        client_mosquitto.on_message = on_mosquitto_message
        client_mosquitto.loop_start() 
        
        client_tb.connect(TB_HOST, 1883, 60) 
        client_tb.subscribe("v1/devices/me/rpc/request/+")
        client_tb.on_message = on_tb_message
        client_tb.loop_start() 
    except Exception as e:
        print(f"Error MQTT: {e}")

@app.get("/")
def read_root(): return {"status": "Online", "state": CURRENT_STATE.value}