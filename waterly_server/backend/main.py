import paho.mqtt.client as mqtt
import json
import time
import os
import requests
from fastapi import FastAPI
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# --- CONFIGURACIÓN ---
TB_ACCESS_TOKEN = "x35f744geqt5lgsnlwsk" 
TB_HOST = "thingsboard"
MOSQUITTO_HOST = "mosquitto"

# Credenciales de Admin (Tenant)
TB_ADMIN_USER = "tenant@thingsboard.org"
TB_ADMIN_PASS = "tenant"

# --- CONFIGURACION INFLUXDB ---
# Datos definidos en el docker-compose
INFLUX_URL = "http://influxdb:8086"
INFLUX_TOKEN = "admin"
INFLUX_ORG = "waterly_org"
INFLUX_BUCKET = "sensor_data"

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
    # Proceso los datos para que no entre basura a la base de datos
    try:
        data = json.loads(raw_json)
        
        # Diccionario limpio que vamos a devolver
        clean_data = {}
        
        # Lista de sensores que me interesan
        sensors = ["uv", "vis", "nir"]
        
        for key, value in data.items():
            if key in sensors:
                # Compruebo que sea un numero
                if isinstance(value, (int, float)):
                    # Reglas de negocio: nada de negativos ni valores locos
                    if value < 0:
                        print(f"Dato descartado (negativo): {key}={value}")
                        continue
                    if value > 10000:
                        print(f"Dato descartado (demasiado alto): {key}={value}")
                        continue
                    
                    # Si pasa los filtros, pa dentro
                    clean_data[key] = value
        
        return clean_data

    except json.JSONDecodeError:
        print("Me ha llegado algo que no es JSON valido")
        return None

def save_to_influx(data):
    # Guardo los datos ya limpios en InfluxDB
    try:
        if not data:
            return

        # Creo el punto de datos
        p = Point("water_quality") \
            .tag("device", "ESP32_01") \
            .field("uv", float(data.get("uv", 0))) \
            .field("vis", float(data.get("vis", 0))) \
            .field("nir", float(data.get("nir", 0)))
        
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)
        print("Datos guardados en InfluxDB correctamente")
        
    except Exception as e:
        print(f"Error escribiendo en InfluxDB: {e}")

def autoconfig_thingsboard():
    print("🔧 [AUTO-CONFIG] Verificando dispositivo en ThingsBoard...")
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
            print(f"🛠️ Creando dispositivo '{device_name}'...")
            new_device = {"name": device_name, "type": "ESP32_Sensor"}
            resp = requests.post(f"{base_url}/api/device", json=new_device, headers=headers)
            if resp.status_code == 200:
                device_id = resp.json()["id"]["id"]
                print(f"Dispositivo creado con éxito.")
            else:
                print(f"Error creando dispositivo: {resp.text}")
                return True 

        # 3. Forzar Token (AQUÍ ESTABA EL CAMBIO CLAVE)
        if device_id:
            # URL para LEER (GET) - Esta sí lleva el ID
            get_url = f"{base_url}/api/device/{device_id}/credentials"
            # URL para GUARDAR (POST) - Esta NO lleva el ID en la URL
            save_url = f"{base_url}/api/device/credentials"

            resp = requests.get(get_url, headers=headers)
            
            if resp.status_code == 200:
                creds_data = resp.json()
                current_token = creds_data.get("credentialsId")
                
                if current_token != TB_ACCESS_TOKEN:
                    print(f"🔄 El token actual es '{current_token}'. Cambiando a '{TB_ACCESS_TOKEN}'...")
                    
                    # Preparamos el paquete
                    # ThingsBoard necesita el deviceId dentro del JSON para saber a quién actualizar
                    payload = {
                        "id": creds_data.get("id"), 
                        "createdTime": creds_data.get("createdTime"),
                        "deviceId": creds_data.get("deviceId"), # <--- Importante: mantener esto
                        "credentialsType": "ACCESS_TOKEN",
                        "credentialsId": TB_ACCESS_TOKEN,
                        "credentialsValue": None
                    }

                    # Enviamos a la URL genérica (save_url)
                    save_resp = requests.post(save_url, json=payload, headers=headers)
                    
                    if save_resp.status_code == 200:
                        print("✨ ¡Token actualizado con ÉXITO!")
                    else:
                        print(f"FALLO al guardar. Código: {save_resp.status_code}")
                        print(f"Respuesta: {save_resp.text}")
                else:
                    print("El Token ya es correcto.")
            return True
            
    except Exception as e:
        return False
    
    return False

# --- PUENTE MQTT ---
def on_mosquitto_message(client, userdata, msg):
    try:
        raw_payload = msg.payload.decode()
        
        # 1. Validar y limpiar datos
        clean_data = clean_and_validate_data(raw_payload)
        
        if clean_data:
            # 2. Si los datos son buenos, los mando a InfluxDB
            save_to_influx(clean_data)
            
            # 3. Y tambien los mando a ThingsBoard para verlos en la grafica
            # Reconstruyo el JSON por si he quitado algun valor malo
            client_tb.publish("v1/devices/me/telemetry", json.dumps(clean_data))
            print(f"Datos procesados: {clean_data}")
        else:
            print("Datos ignorados por no cumplir validacion")

    except Exception as e:
        print(f"Error en el puente: {e}")

def on_tb_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
        method = data.get("method")
        params = data.get("params")
        esp_payload = {}
        
        if method == "setTraining": esp_payload = {"training": params}
        elif method == "deepSleep": esp_payload = {"training": False} 
        
        if esp_payload:
            client_mosquitto.publish("waterly/comandos", json.dumps(esp_payload), retain=True)
            print(f"[CMD] Enviado: {json.dumps(esp_payload)}")
    except: pass

# --- ARRANQUE ---
@app.on_event("startup")
def start_bridge():
    print(">>> INICIANDO WATERLY BRAIN v2.0 <<<")
    
    tb_ready = False
    for i in range(30): 
        if autoconfig_thingsboard():
            print("🟢 ThingsBoard configurado.")
            tb_ready = True
            break
        print(f"⏳ Intento {i+1}/30: Esperando a ThingsBoard...")
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
        print("ThingsBoard MQTT Conectado")
    except Exception as e:
        print(f"Error MQTT: {e}")

@app.get("/")
def read_root(): return {"status": "Online"}