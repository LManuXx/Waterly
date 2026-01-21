import paho.mqtt.client as mqtt
import json
import time
import os
from fastapi import FastAPI

# --- CONFIGURACIÓN ---
# 1. Credenciales y Direcciones
# Token copiado de ThingsBoard
TB_ACCESS_TOKEN = "x35f744geqt5lgsnlwsk" 
TB_HOST = "thingsboard"  # Nombre del servicio en Docker
MOSQUITTO_HOST = "mosquitto" # Nombre del servicio en Docker

# 2. Clientes MQTT
# Cliente para hablar con el ESP32 (Mosquitto)
client_mosquitto = mqtt.Client(client_id="Bridge_To_Mosquitto")
# Cliente para hablar con la Plataforma (ThingsBoard)
client_tb = mqtt.Client(client_id="Bridge_To_ThingsBoard")

# Configuramos el usuario de ThingsBoard (así se autentica)
client_tb.username_pw_set(TB_ACCESS_TOKEN)

app = FastAPI()


def on_mosquitto_message(client, userdata, msg):
    """
    [ESP32 -> THINGSBOARD]
    Recibe datos del sensor y los sube a la nube.
    """
    try:
        payload = msg.payload.decode()
        print(f"[ESP32 -> API] Dato recibido: {payload}")
        
        # Reenviar a ThingsBoard
        client_tb.publish("v1/devices/me/telemetry", payload)
        print("[API -> TB] Dato reenviado a ThingsBoard")
        
        
    except Exception as e:
        print(f"Error procesando mensaje hacia TB: {e}")

def on_tb_message(client, userdata, msg):
    """
    [THINGSBOARD -> ESP32]
    Recibe clics en botones y los baja al dispositivo.
    """
    try:
        print(f"[TB -> API] Orden recibida: {msg.topic} {msg.payload}")
        data = json.loads(msg.payload)
        method = data.get("method")
        params = data.get("params")
        
        esp_payload = {}
        
        # --- TRADUCCIÓN DE ÓRDENES ---
        
        # CASO 1: Interruptor de Entrenamiento
        # En TB el método debe llamarse: setTraining
        if method == "setTraining":
            esp_payload = {"training": params} # params será true o false
            
        # CASO 2: Botón de Dormir
        # En TB el método debe llamarse: deepSleep
        elif method == "deepSleep":
             # Forzamos apagado de entrenamiento, lo que lleva al sleep en tu código C
             esp_payload = {"training": False} 
        
        # --- ENVÍO A MOSQUITTO ---
        if esp_payload:
            payload_str = json.dumps(esp_payload)
            
            # IMPORTANTE: retain=True
            # Esto hace que si el ESP32 está durmiendo, el mensaje le espere
            # hasta que se despierte y se conecte.
            client_mosquitto.publish("waterly/comandos", payload_str, retain=True)
            
            print(f"[API -> ESP32] Orden enviada (Retained): {payload_str}")
            
    except Exception as e:
        print(f"Error procesando RPC desde TB: {e}")


@app.on_event("startup")
def start_bridge():
    print(">>> INICIANDO WATERLY BRAIN (BRIDGE MODE) <<<")
    
    # 1. Conectar a Mosquitto (Escucha al ESP32)
    try:
        client_mosquitto.connect(MOSQUITTO_HOST, 1883, 60)
        client_mosquitto.subscribe("waterly/datos")
        client_mosquitto.on_message = on_mosquitto_message
        client_mosquitto.loop_start() 
        print("Conectado a Mosquitto (Broker Local)")
    except Exception as e:
        print(f"Error conectando a Mosquitto: {e}")

    # 2. Conectar a ThingsBoard (Envía a la Nube)
    try:
        client_tb.connect(TB_HOST, 1883, 60) 
        # Nos suscribimos a los comandos RPC (Botones del Dashboard)
        client_tb.subscribe("v1/devices/me/rpc/request/+")
        client_tb.on_message = on_tb_message
        client_tb.loop_start() 
        print("Conectado a ThingsBoard (Dashboard)")
    except Exception as e:
        print(f"Error conectando a ThingsBoard: {e}")

@app.get("/")
def read_root():
    return {
        "status": "Bridge Online", 
        "mode": "ThingsBoard <-> Mosquitto",
        "token": "Configured"
    }