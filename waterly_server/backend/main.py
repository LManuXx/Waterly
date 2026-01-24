import paho.mqtt.client as mqtt
import json
import time
import os
import requests
from fastapi import FastAPI
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

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

def save_to_influx(data):
    """
    MODIFICADO: Guarda dinámicamente todos los campos recibidos
    """
    try:
        if not data: return

        # Creamos el Punto base
        p = Point("water_quality") \
            .tag("device", "ESP32_01") \
            .tag("component", data["target"])

        # Bucle Mágico: Añadimos cada lectura como un 'field' automáticamente
        for key, val in data.items():
            if key != "target": # La etiqueta ya la usamos arriba como tag, no la duplicamos como field
                p.field(key, float(val))
        
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)
        
        # Log resumido (Muestra cantidad de campos guardados)
        print(f"InfluxDB: Guardados {len(data)-1} canales | Target: {data['target']}")
        
    except Exception as e:
        print(f"Error escribiendo en InfluxDB: {e}")

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

# --- PUENTE MQTT (ESP32 -> NUBE) ---
def on_mosquitto_message(client, userdata, msg):
    try:
        raw_payload = msg.payload.decode()
        
        # 1. Validar y limpiar (Ahora acepta los 18 canales)
        clean_data = clean_and_validate_data(raw_payload)
        
        if clean_data:
            # 2. Guardar en InfluxDB (Guarda los 18 campos dinámicamente)
            save_to_influx(clean_data)
            
            # 3. Enviar a ThingsBoard (ThingsBoard crea las variables automáticamente al recibirlas)
            client_tb.publish("v1/devices/me/telemetry", json.dumps(clean_data))
        else:
            pass

    except Exception as e:
        print(f"Error en el puente de datos: {e}")

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
        
        if esp_payload:
            client_mosquitto.publish("waterly/comandos", json.dumps(esp_payload), retain=should_retain)
            print(f"[CMD] Enviado a ESP32: {method} | Retain: {should_retain}")
            
    except Exception as e:
        print(f"Error gestionando RPC: {e}")

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