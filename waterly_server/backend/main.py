from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import paho.mqtt.client as mqtt
import json
import time

app = FastAPI(title="Waterly Brain API")

# --- CONFIGURACIÓN ---
# IMPORTANTE: "mosquitto" es el nombre del servicio en docker-compose
MQTT_BROKER = "mosquitto" 
MQTT_PORT = 1883
TOPIC_CMD = "waterly/comandos"

class DeepSleepRequest(BaseModel):
    minutes: int

class TrainingModeRequest(BaseModel):
    enabled: bool

# --- HELPER MQTT ---
def send_mqtt_cmd(payload: dict):
    try:
        # Creamos cliente nuevo para cada petición (simple y robusto)
        client = mqtt.Client(client_id="waterly_api_sender")
        client.connect(MQTT_BROKER, MQTT_PORT, 5)
        
        msg = json.dumps(payload)
        # Retain=True es CLAVE: Si el ESP32 duerme, el mensaje le espera
        client.publish(TOPIC_CMD, msg, retain=True)
        
        client.disconnect()
        return True
    except Exception as e:
        print(f"Error MQTT: {e}")
        return False

# --- ENDPOINTS (Tus botones de control) ---

@app.get("/")
def read_root():
    return {"status": "Waterly Brain is Online"}

@app.post("/control/deep-sleep")
def set_deep_sleep(request: DeepSleepRequest):
    """Manda al ESP32 a dormir X minutos"""
    payload = {
        "cmd": "deep_sleep",
        "duration_sec": request.minutes * 60
    }
    
    success = send_mqtt_cmd(payload)
    if not success:
        raise HTTPException(status_code=500, detail="Fallo al conectar con MQTT")
    
    return {"status": "orden_enviada", "data": payload}

@app.post("/control/training")
def set_training_mode(request: TrainingModeRequest):
    """Activa o desactiva el modo entrenamiento"""
    payload = {
        "cmd": "training_mode",
        "value": request.enabled
    }
    
    success = send_mqtt_cmd(payload)
    if not success:
        raise HTTPException(status_code=500, detail="Fallo al conectar con MQTT")
        
    return {"status": "orden_enviada", "data": payload}