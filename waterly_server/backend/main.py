import paho.mqtt.client as mqtt
import json
import time
import os
import requests
from fastapi import FastAPI
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from brain import SpectralBrain

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
CURRENT_LABEL = "Unknown" 

CALIBRATION_STATE = {
    "active": False,
    "target_count": 0,
    "current_buffer": []
}

brain = SpectralBrain()

try:
    client_influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client_influx.write_api(write_options=SYNCHRONOUS)
    print("Conexion con InfluxDB lista")
except Exception as e:
    print(f"Fallo al conectar con InfluxDB: {e}")

# Clientes MQTT
client_mosquitto = mqtt.Client(client_id="Bridge_To_Mosquitto")
client_tb = mqtt.Client(client_id="Bridge_To_ThingsBoard")
client_tb.username_pw_set(TB_ACCESS_TOKEN)

app = FastAPI()

def clean_and_validate_data(raw_json):
    """
    MODIFICADO: Ahora es dinámico. Acepta cualquier clave numérica
    que envíe el ESP32 (A_410nm, G_560nm, etc.)
    """
    try:
        data = json.loads(raw_json)
        clean_data = {}
        
        # Recorremos TODAS las claves que vienen en el JSON
        for key, val in data.items():
            # Si el valor es un número (int o float), lo guardamos
            if isinstance(val, (int, float)):
                # Filtro de seguridad: ignorar valores corruptos extremos
                if -1000 <= val <= 100000: 
                    clean_data[key] = val
        
        # Inyectamos la etiqueta actual
        clean_data["target"] = CURRENT_LABEL
        
        # Si hemos recuperado al menos 1 dato numérico, es válido
        if len(clean_data) > 1: # >1 porque "target" siempre está
            return clean_data
        
        return None

    except json.JSONDecodeError:
        print("JSON invalido recibido")
        return None

def save_to_influx(data, is_absorbance=False):
    try:
        if not data: return
        
        # Marcamos si son datos Crudos (Raw) o Absorbancia (Abs)
        measurement_name = "water_quality_abs" if is_absorbance else "water_quality_raw"

        p = Point(measurement_name) \
            .tag("device", "ESP32_01") \
            .tag("component", CURRENT_LABEL)

        for key, val in data.items():
            # Guardamos las coordenadas PCA y los espectros
            if key in ["pc1", "pc2"] or "_nm" in key:
                p.field(key, float(val))
        
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)
        print(f"InfluxDB ({measurement_name}): Guardado.")
        
    except Exception as e:
        print(f"Error InfluxDB: {e}")

def autoconfig_thingsboard():
    # ... (Esta función NO cambia, es correcta) ...
    print("[AUTO-CONFIG] Verificando dispositivo en ThingsBoard...")
    base_url = f"http://{TB_HOST}:9090"
    
    try:
        # 1. Login
        resp = requests.post(f"{base_url}/api/auth/login", json={"username": TB_ADMIN_USER, "password": TB_ADMIN_PASS})
        if resp.status_code != 200:
            return False 
            
        headers = {"X-Authorization": f"Bearer {resp.json()['token']}"}
        
        # 2. Buscar/Crear Dispositivo
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
                print(f"Error creando dispositivo: {resp.text}")
                return True 

        # 3. Forzar Token
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
                    
                    if save_resp.status_code == 200:
                        print("Token actualizado con EXITO")
                    else:
                        print(f"FALLO al guardar token: {save_resp.status_code}")
                else:
                    print("El Token ya es correcto.")
            return True
            
    except Exception as e:
        return False
    
    return False

def on_mosquitto_message(client, userdata, msg):
    global CALIBRATION_STATE
    
    try:
        raw_payload = msg.payload.decode()
        raw_data = clean_and_validate_data(raw_payload)
        
        if raw_data:
            # --- ZONA DE CALIBRACIÓN MULTI-MUESTRA ---
            if CALIBRATION_STATE["active"]:
                CALIBRATION_STATE["current_buffer"].append(raw_data)
                count = len(CALIBRATION_STATE["current_buffer"])
                target = CALIBRATION_STATE["target_count"]
                print(f"[CALIBRANDO] Muestra recibida {count}/{target}")
                
                if count < target:
                    time.sleep(0.1) 
                    client_mosquitto.publish("waterly/comandos", json.dumps({"mode": "single"}), retain=False)
                else:
                    print("[CALIBRANDO] Calculando promedio...")
                    avg_data = calculate_average_spectrum(CALIBRATION_STATE["current_buffer"])
                    if avg_data:
                        brain.calibrate(avg_data)
                        print(f"--> ÉXITO: Línea base establecida.")
                    else:
                        print("--> ERROR: No se pudo promediar.")
                    CALIBRATION_STATE["active"] = False
                    CALIBRATION_STATE["current_buffer"] = []
                return 
            # -------------------------------------------

            # 1. Guardar RAW en Influx
            raw_data["target"] = CURRENT_LABEL
            save_to_influx(raw_data, is_absorbance=False)
            
            # 2. Calcular Absorbancia
            abs_data, is_calibrated = brain.get_absorbance(raw_data)
            
            pc1, pc2 = 0.0, 0.0
            if is_calibrated:
                brain.update_pca(abs_data)
                pc1, pc2 = brain.get_coords(abs_data)
            
            # 3. EMPAQUETADO MIXTO
            final_package = abs_data.copy()

            # A) Limpieza de negativos
            for k, v in final_package.items():
                if isinstance(v, (int, float)) and v < 0:
                     # Solo limpiamos si parece un canal espectral (tiene números en el nombre)
                     if any(char.isdigit() for char in k):
                        final_package[k] = 0.0

            # B) Inyectar RAW (VERSIÓN NUCLEAR DEBUG)
            # Imprimimos para ver qué está intentando procesar
            print(f"[DEBUG RAW] Procesando {len(raw_data)} claves originales...")
            
            for k, v in raw_data.items():
                # Lógica simplificada: Si no es 'target' y es un número, PA DENTRO.
                if k != "target":
                    # Forzamos que sea float para evitar problemas de tipos
                    try:
                        val_float = float(v)
                        final_package[f"raw_{k}"] = val_float
                        # print(f" -> Añadido: raw_{k}") # Descomenta si necesitas ver uno a uno
                    except ValueError:
                        pass # Si no es convertible a número, lo ignoramos

            # Añadir metadatos
            final_package["target"] = CURRENT_LABEL
            final_package["pc1"] = pc1
            final_package["pc2"] = pc2
            final_package["calibrated"] = is_calibrated 

            # DEBUG: Vemos las claves finales
            keys_list = list(final_package.keys())
            # Filtramos solo las raw para ver si están
            raw_keys = [k for k in keys_list if "raw_" in k]
            print(f"[DEBUG FINAL] Total Keys: {len(keys_list)} | Raw Keys insertadas: {len(raw_keys)}")
            if len(raw_keys) > 0:
                print(f"[DEBUG SAMPLE] Ejemplo: {raw_keys[0]}")

            # 4. Enviar
            save_to_influx(abs_data, is_absorbance=True)
            client_tb.publish("v1/devices/me/telemetry", json.dumps(final_package))
            
    except Exception as e:
        print(f"Error pipeline CRITICO: {e}")



def calculate_average_spectrum(buffer):
    """ Toma una lista de N mediciones y devuelve un solo diccionario promediado """
    if not buffer: return None
    
    first_sample = buffer[0]
    averaged_data = {}
    
    # Recorremos las claves (A_410nm, B_435nm, etc.)
    for key in first_sample.keys():
        # Solo promediamos los números
        if isinstance(first_sample[key], (int, float)):
            total = sum(d.get(key, 0) for d in buffer)
            averaged_data[key] = round(total / len(buffer), 2)
            
    return averaged_data

# --- PUENTE MQTT (NUBE -> ESP32) ---
def on_tb_message(client, userdata, msg):
    # ... (Esta función NO cambia, es correcta para recibir comandos) ...
    global CURRENT_LABEL
    
    try:
        data = json.loads(msg.payload)
        method = data.get("method")
        params = data.get("params")
        
        esp_payload = {}
        should_retain = False 
        
        if method == "setTarget":
            CURRENT_LABEL = str(params)
            print(f"[API] Etiqueta cambiada a: {CURRENT_LABEL}")
            return 

        elif method == "setIdle":
            esp_payload = {"mode": "idle"}
            should_retain = True 
            
        elif method == "startTraining":
            esp_payload = {"mode": "training"}
            should_retain = True
            
        elif method == "deepSleep":
            esp_payload = {"mode": "sleep"}
            should_retain = True
            
        elif method == "singleMeasure":
            esp_payload = {"mode": "single"}
            should_retain = False 
            
        elif method == "startOTA":
            esp_payload = {"update": True}
            should_retain = False 

        elif method == "calibrate":
            # Leemos el número de muestras (si viene vacío, por defecto 10)
            samples = int(params) if params else 10
            
            print(f"[CALIBRACIÓN] Iniciando secuencia de {samples} muestras...")
            
            # Preparamos el estado
            CALIBRATION_STATE["active"] = True
            CALIBRATION_STATE["target_count"] = samples
            CALIBRATION_STATE["current_buffer"] = []
            
            # Lanzamos la PRIMERA petición
            client_mosquitto.publish("waterly/comandos", json.dumps({"mode": "single"}), retain=False)
        
        if esp_payload:
            client_mosquitto.publish("waterly/comandos", json.dumps(esp_payload), retain=should_retain)
            print(f"[CMD] Enviado a ESP32: {method} | Retain: {should_retain}")
            
    except Exception as e:
        print(f"Error gestionando RPC: {e}")

waiting_for_calibration = False

# --- ARRANQUE ---
@app.on_event("startup")
def start_bridge():
    print(">>> INICIANDO WATERLY BRAIN v2.2 (Full Spectrum Support) <<<")
    
    # ... (Resto del arranque igual) ...
    tb_ready = False
    for i in range(30): 
        if autoconfig_thingsboard():
            print("ThingsBoard configurado.")
            tb_ready = True
            break
        print(f"Intento {i+1}/30: Esperando a ThingsBoard...")
        time.sleep(5)

    try:
        client_mosquitto.connect(MOSQUITTO_HOST, 1883, 60)
        client_mosquitto.subscribe("waterly/datos")
        client_mosquitto.on_message = on_mosquitto_message
        client_mosquitto.loop_start() 
        print("Mosquitto Conectado")
        
        client_tb.connect(TB_HOST, 1883, 60) 
        client_tb.subscribe("v1/devices/me/rpc/request/+")
        client_tb.on_message = on_tb_message
        client_tb.loop_start() 
        print("ThingsBoard RPC Conectado")
    except Exception as e:
        print(f"Error MQTT: {e}")

@app.get("/")
def read_root(): return {"status": "Online", "current_target": CURRENT_LABEL}