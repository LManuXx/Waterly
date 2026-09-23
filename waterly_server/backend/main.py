import paho.mqtt.client as mqtt
import json
import time
import threading
import os
import asyncio
import requests
import shutil
from typing import Any, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import ASYNCHRONOUS
from brain import SpectralBrain
from enum import Enum
import hub
import commands
import mqtt_topics

class SystemState(Enum):
    IDLE = "IDLE"
    CALIBRATION = "CALIBRATION"
    FREE_MEASURE = "FREE_MEASURE"
    TRAINING = "TRAINING"
    ANALYSIS = "ANALYSIS"
    BLANK_CHECK = "BLANK_CHECK"

# --- CONFIGURACION ---
TB_ENABLED = os.environ.get("TB_ENABLED", "false").lower() in ("1", "true", "yes")
TB_ACCESS_TOKEN = os.environ.get("TB_ACCESS_TOKEN", "x35f744geqt5lgsnlwsk")
TB_HOST = os.environ.get("TB_HOST", "thingsboard")
MOSQUITTO_HOST = os.environ.get("MOSQUITTO_HOST", os.environ.get("MQTT_BROKER", "mosquitto"))

# Credenciales de Admin (Tenant)
TB_ADMIN_USER = os.environ.get("TB_ADMIN_USER", "tenant@thingsboard.org")
TB_ADMIN_PASS = os.environ.get("TB_ADMIN_PASS", "tenant")

# --- CONFIGURACION INFLUXDB ---
INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "admin")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "waterly_org")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "sensor_data")

# --- MEMORIA DE LA API Y CONCURRENCIA ---
CURRENT_STATE = SystemState.IDLE
state_lock = threading.RLock()
BURST_START_TIME = 0.0
BURST_MIN_INTERVAL_S = 0.25  # Residuos en vuelo tras parar monitor (1ª single a ~0.5s)
BURST_PACE_S = 0.5  # Espera entre lecturas del burst
BURST_MAX_QC_FAILS = 3  # Tras N QC fail consecutivos, abortar (no bucle infinito)
BURST_CONTEXT = {
    "target_count": 0,
    "current_buffer": [],
    "label": "Unknown",
    "qc_fails": 0,
}
BURST_TIMER = None
BURST_PACE_TIMER = None
BURST_TIMEOUT_S = 45
CURRENT_CONFIG = {}

FIRMWARE_DIR = "/app/firmware"
FIRMWARE_BIN = os.path.join(FIRMWARE_DIR, "waterly.bin")
FIRMWARE_VERSION_JSON = os.path.join(FIRMWARE_DIR, "version.json")

brain = SpectralBrain()

client_influx = None
write_api = None
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

app = FastAPI(title="Waterly API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def publish_status(partial: dict):
    """Actualiza hub (WS + memoria) y TB si está habilitado."""
    hub.update_telemetry(partial, client_tb=client_tb if TB_ENABLED else None, tb_enabled=TB_ENABLED)


def _wire_commands():
    def _set_state(s):
        global CURRENT_STATE
        CURRENT_STATE = s

    def _set_burst_context(c):
        global BURST_CONTEXT
        BURST_CONTEXT = c

    def _set_burst_timer(t):
        global BURST_TIMER
        BURST_TIMER = t

    def _set_burst_start(t):
        global BURST_START_TIME
        BURST_START_TIME = t

    commands.ctx.SystemState = SystemState
    commands.ctx.state_lock = state_lock
    commands.ctx.get_state = lambda: CURRENT_STATE
    commands.ctx.set_state = _set_state
    commands.ctx.get_burst_context = lambda: BURST_CONTEXT
    commands.ctx.set_burst_context = _set_burst_context
    commands.ctx.get_burst_timer = lambda: BURST_TIMER
    commands.ctx.set_burst_timer = _set_burst_timer
    commands.ctx.set_burst_start = _set_burst_start
    commands.ctx.reset_burst_timer = reset_burst_timer
    commands.ctx.cancel_burst_pace_timer = cancel_burst_pace_timer
    commands.ctx.request_next_burst_sample = request_next_burst_sample
    commands.ctx.client_mosquitto = client_mosquitto
    commands.ctx.brain = brain
    commands.ctx.publish_status = publish_status
    commands.ctx.FIRMWARE_BIN = FIRMWARE_BIN
    commands.ctx.FIRMWARE_VERSION_JSON = FIRMWARE_VERSION_JSON
    commands.ctx.get_current_config = lambda: CURRENT_CONFIG

def parse_rpc_params(params):
    """
    Parsea parámetros RPC procedentes de ThingsBoard, soportando:
    - Cadenas JSON escapadas (ej: '{"label": "100.0", "samples": 5}')
    - Diccionarios nativos
    - Números directos en string o valor primitivo
    """
    if isinstance(params, str):
        p_str = params.strip()
        if (p_str.startswith("{") and p_str.endswith("}")) or (p_str.startswith("[") and p_str.endswith("]")):
            try:
                return json.loads(p_str)
            except Exception:
                pass
        try:
            if "." in p_str:
                return float(p_str)
            return int(p_str)
        except ValueError:
            return p_str
    return params

def clean_and_validate_data(raw_json):
    """
    Valida y limpia el JSON recibido por MQTT.
    Verifica que contenga las 18 longitudes de onda esperadas del sensor AS7265x.
    """
    try:
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            return None
        clean_data = {}

        for key, val in data.items():
            if isinstance(val, (int, float)):
                if -100 <= val <= 100000:
                    clean_data[key] = float(val)

        # Verificar que contiene los 18 canales espectrales requeridos
        expected_channels = brain._get_wavelengths()
        has_all_channels = all((ch in clean_data or f"raw_{ch}" in clean_data) for ch in expected_channels)
        if has_all_channels:
            return clean_data

        print(f"[DATA] Muestra incompleta descartada ({len(clean_data)} canales recibidos)")
        return None

    except json.JSONDecodeError:
        print("[DATA] JSON invalido recibido por MQTT")
        return None

def save_to_influx(data, is_absorbance=False):
    """Persiste espectro en Influx: raw_* → water_quality_raw, *_abs → water_quality_abs."""
    try:
        if not data or write_api is None:
            return

        raw_fields = {}
        abs_fields = {}
        for key, val in data.items():
            if not isinstance(val, (int, float)):
                continue
            if key.startswith("raw_") and key.endswith("nm"):
                raw_fields[key] = float(val)
            elif key.endswith("_abs") or (key.endswith("nm") and not key.startswith("raw_")):
                abs_fields[key] = float(val)
            elif key in ("pc1", "pc2", "pred_deviation", "pred_mg_l"):
                raw_fields[key] = float(val)

        state_tag = CURRENT_STATE.value
        if raw_fields:
            p = Point("water_quality_raw").tag("device", "ESP32_01").tag("state", state_tag)
            for k, v in raw_fields.items():
                p.field(k, v)
            write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)

        if abs_fields:
            p = Point("water_quality_abs").tag("device", "ESP32_01").tag("state", state_tag)
            for k, v in abs_fields.items():
                p.field(k, v)
            write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)

    except Exception as e:
        print(f"Error InfluxDB: {e}")


def save_prediction_to_influx(pred_result, source="analysis", qc_level="ok"):
    """Guarda un resultado de concentración/desviación para el historial de resultados."""
    try:
        if not pred_result or write_api is None:
            return
        p = (
            Point("water_quality_pred")
            .tag("device", "ESP32_01")
            .tag("kind", str(pred_result.get("kind") or "unknown"))
            .tag("source", source)
            .tag("confidence", str(pred_result.get("confidence") or "ok"))
            .tag("qc", qc_level)
            .tag("mode", brain.model_type)
        )
        if pred_result.get("pred_mg_l") is not None:
            p = p.field("pred_mg_l", float(pred_result["pred_mg_l"]))
        if pred_result.get("pred_deviation") is not None:
            p = p.field("pred_deviation", float(pred_result["pred_deviation"]))
        if pred_result.get("value") is not None:
            p = p.field("value", float(pred_result["value"]))
        if pred_result.get("t2") is not None:
            p = p.field("t2", float(pred_result["t2"]))
        if pred_result.get("q") is not None:
            p = p.field("q", float(pred_result["q"]))
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=p)
    except Exception as e:
        print(f"Error InfluxDB pred: {e}")


def _pack_prediction(pred_result):
    if not pred_result:
        return {
            "pred_deviation": None,
            "pred_mg_l": None,
            "pred_unit": None,
            "pred_kind": None,
            "pred_confidence": None,
            "pred_confidence_label": None,
            "pred_in_model": None,
            "pred_by_model": None,
            "pred_consensus": None,
            "pred_consensus_method": None,
            "pred_consensus_weighted": None,
            "pred_consensus_label": None,
            "pred_agreement": None,
            "pred_spread": None,
            "pred_t2": None,
            "pred_q": None,
        }
    return {
        "pred_deviation": pred_result.get("pred_deviation"),
        "pred_mg_l": pred_result.get("pred_mg_l"),
        "pred_unit": pred_result.get("unit"),
        "pred_kind": pred_result.get("kind"),
        "pred_confidence": pred_result.get("confidence"),
        "pred_confidence_label": pred_result.get("confidence_label"),
        "pred_in_model": pred_result.get("in_model"),
        "pred_by_model": pred_result.get("pred_by_model"),
        "pred_consensus": pred_result.get("pred_consensus"),
        "pred_consensus_method": pred_result.get("pred_consensus_method"),
        "pred_consensus_weighted": pred_result.get("pred_consensus_weighted"),
        "pred_consensus_label": pred_result.get("pred_consensus_label"),
        "pred_agreement": pred_result.get("pred_agreement"),
        "pred_spread": pred_result.get("pred_spread"),
        "pred_t2": pred_result.get("t2"),
        "pred_q": pred_result.get("q"),
        # legacy: mismo valor numérico principal
        "pred_value": pred_result.get("value"),
    }
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

def cancel_burst_pace_timer():
    global BURST_PACE_TIMER
    if BURST_PACE_TIMER is not None:
        BURST_PACE_TIMER.cancel()
        BURST_PACE_TIMER = None

def request_next_burst_sample(delay_s=None):
    """Pide el siguiente mode:single tras delay (evita acumulación de lecturas)."""
    global BURST_PACE_TIMER
    if delay_s is None:
        delay_s = BURST_PACE_S
    cancel_burst_pace_timer()

    def _fire():
        global BURST_PACE_TIMER
        BURST_PACE_TIMER = None
        with state_lock:
            if CURRENT_STATE not in (
                SystemState.CALIBRATION,
                SystemState.TRAINING,
                SystemState.ANALYSIS,
                SystemState.BLANK_CHECK,
            ):
                return
            buf = BURST_CONTEXT.get("current_buffer") or []
            target = int(BURST_CONTEXT.get("target_count") or 0)
            if len(buf) >= target:
                return
        try:
            client_mosquitto.publish(
                mqtt_topics.TOPIC_CMD, json.dumps({"mode": "single"}), qos=1, retain=False
            )
        except Exception as e:
            print(f"[BURST] Error pidiendo siguiente muestra: {e}")

    if delay_s <= 0:
        _fire()
        return
    BURST_PACE_TIMER = threading.Timer(delay_s, _fire)
    BURST_PACE_TIMER.daemon = True
    BURST_PACE_TIMER.start()

def reset_burst_timer():
    global BURST_TIMER
    if BURST_TIMER is not None:
        BURST_TIMER.cancel()
    BURST_TIMER = threading.Timer(BURST_TIMEOUT_S, burst_timeout_handler)
    BURST_TIMER.daemon = True
    BURST_TIMER.start()

def abort_burst(reason: str):
    """Cancela el burst activo y vuelve a IDLE con mensaje claro."""
    global CURRENT_STATE, BURST_CONTEXT, BURST_TIMER
    cancel_burst_pace_timer()
    if BURST_TIMER is not None:
        BURST_TIMER.cancel()
        BURST_TIMER = None
    with state_lock:
        was = CURRENT_STATE.value
        CURRENT_STATE = SystemState.IDLE
        BURST_CONTEXT = {"target_count": 0, "current_buffer": [], "label": "Unknown", "qc_fails": 0}
    try:
        client_mosquitto.publish(mqtt_topics.TOPIC_CMD, json.dumps({"mode": "idle"}), qos=1, retain=False)
    except Exception as e:
        print(f"[BURST] Error idle tras abort: {e}")
    print(f"[BURST] Abortado desde {was}: {reason}")
    publish_status({
        "prediction_status": reason,
        "system_state": "IDLE",
        "burst_count": 0,
        "burst_target": 0,
    })


def burst_timeout_handler():
    with state_lock:
        active = CURRENT_STATE in _BURST_STATES
        old = CURRENT_STATE.value if active else None
    if active:
        abort_burst(f"Timeout en {old}: operación cancelada (sensor no respondió a tiempo)")


def _decision_fields():
    """Campos de ayuda a la decisión para telemetría/status."""
    y_min, y_max = brain.train_range()
    # Prefer metrics from trained model if present
    if brain.is_trained and brain.metrics:
        y_min = brain.metrics.get("y_min", y_min)
        y_max = brain.metrics.get("y_max", y_max)
    return {
        "n_samples": len(brain.dataset_X),
        "sample_labels": brain.sample_labels(),
        "concentration_counts": brain.concentration_counts(),
        "train_y_min": y_min,
        "train_y_max": y_max,
        "has_baseline": brain.baseline is not None,
        "baseline_ts": brain.baseline_ts,
    }


_BURST_STATES = (
    SystemState.CALIBRATION,
    SystemState.TRAINING,
    SystemState.ANALYSIS,
    SystemState.BLANK_CHECK,
)


def send_telemetry(
    raw_data,
    abs_data=None,
    is_calibrated=False,
    prediction_status=None,
    prediction_value=None,
    is_progress=False,
    pred_result=None,
    spectrum_qc=None,
    save_pred=False,
    pred_source="analysis",
):
    final_package = {}
    with state_lock:
        final_package["system_state"] = CURRENT_STATE.value
    final_package["calibrated"] = is_calibrated
    final_package["blank_ok"] = bool(is_calibrated and brain.baseline is not None)
    final_package["optics_mismatch"] = bool(brain.optics_mismatch(CURRENT_CONFIG))
    final_package["model_mode"] = brain.model_type
    final_package["model_ready"] = bool(brain.is_trained)
    final_package.update(_decision_fields())
    # Fuera de burst activo, progreso a 0 (salvo is_progress)
    if not is_progress:
        with state_lock:
            in_burst = CURRENT_STATE in _BURST_STATES
        if not in_burst:
            final_package["burst_count"] = 0
            final_package["burst_target"] = 0

    if prediction_status:
        final_package["prediction_status"] = str(prediction_status)

    # pred_result preferente; prediction_value legacy → empaquetar según modo
    if pred_result is None and prediction_value is not None:
        if brain.model_type == "PLSR":
            pred_result = {
                "value": prediction_value,
                "pred_mg_l": prediction_value,
                "pred_deviation": None,
                "unit": "mg/L",
                "kind": "concentration",
                "confidence": "ok",
                "confidence_label": "Predicción",
                "in_model": True,
            }
        else:
            pred_result = {
                "value": prediction_value,
                "pred_mg_l": None,
                "pred_deviation": prediction_value,
                "unit": "índice",
                "kind": "deviation",
                "confidence": "ok",
                "confidence_label": "Índice de desviación (no es mg/L)",
                "in_model": True,
            }
    if pred_result is not None:
        final_package.update(_pack_prediction(pred_result))

    if spectrum_qc:
        final_package["spectrum_qc"] = spectrum_qc.get("level")
        final_package["spectrum_qc_label"] = spectrum_qc.get("label")
        final_package["spectrum_qc_reasons"] = spectrum_qc.get("reasons")

    if not is_progress:
        for k, v in raw_data.items():
            if isinstance(v, (int, float)):
                final_package[f"raw_{k}"] = v
        if abs_data:
            final_package.update(abs_data)
        final_package["spectrum_ts"] = time.time()
        save_to_influx(final_package, is_absorbance=bool(abs_data))
        if save_pred and pred_result and pred_result.get("value") is not None:
            save_prediction_to_influx(
                pred_result,
                source=pred_source,
                qc_level=(spectrum_qc or {}).get("level", "ok"),
            )
    else:
        if prediction_status:
            final_package["prediction_status"] = f"[PROGRESS] {prediction_status}"

    publish_status(final_package)

def on_mosquitto_message(client, userdata, msg):
    global CURRENT_STATE, BURST_CONTEXT, BURST_TIMER, CURRENT_CONFIG

    # Configuracion del ESP32 (topic separado)
    if msg.topic == mqtt_topics.TOPIC_CFG:
        try:
            CURRENT_CONFIG = json.loads(msg.payload.decode("utf-8", errors="ignore"))
            old_cmd, old_dat = mqtt_topics.TOPIC_CMD, mqtt_topics.TOPIC_DAT
            mqtt_topics.apply_from_esp_config(CURRENT_CONFIG)
            if mqtt_topics.TOPIC_DAT != old_dat:
                try:
                    client.unsubscribe(old_dat)
                except Exception:
                    pass
                client.subscribe(mqtt_topics.TOPIC_DAT)
            print(
                f"[CONFIG] Config actualizada desde ESP32: SSID={CURRENT_CONFIG.get('wifi_ssid', '?')} "
                f"cmd={mqtt_topics.TOPIC_CMD} dat={mqtt_topics.TOPIC_DAT}"
                + (f" (resub dat {old_dat}->{mqtt_topics.TOPIC_DAT})" if mqtt_topics.TOPIC_DAT != old_dat else "")
                + (f" (cmd {old_cmd}->{mqtt_topics.TOPIC_CMD})" if mqtt_topics.TOPIC_CMD != old_cmd else "")
            )
        except Exception as e:
            print(f"[CONFIG] Error parseando config: {e}")
        return

    if msg.topic != mqtt_topics.TOPIC_DAT:
        return

    try:
        raw_payload = msg.payload.decode("utf-8", errors="ignore")
        raw_data = clean_and_validate_data(raw_payload)
        if not raw_data:
            return

        qc = brain.assess_spectrum_qc(raw_data)
        abs_data, is_calibrated, snv_data = brain.get_absorbance(raw_data)

        with state_lock:
            state = CURRENT_STATE

            if state == SystemState.IDLE:
                send_telemetry(raw_data, abs_data, is_calibrated, spectrum_qc=qc)
                return

            elif state == SystemState.FREE_MEASURE:
                prediction_status = "Monitorizando"
                pred_result = None
                if not qc["ok"]:
                    prediction_status = f"QC: {qc['label']}"
                elif is_calibrated and brain.is_trained:
                    if brain.optics_mismatch(CURRENT_CONFIG):
                        prediction_status = "Óptica ≠ blanco — recalibra"
                    else:
                        pred_result = brain.predict_result(snv_data)
                elif not is_calibrated:
                    prediction_status = "Falta Calibrar"
                elif not brain.is_trained:
                    prediction_status = "Falta Entrenar"

                send_telemetry(
                    raw_data, abs_data, is_calibrated, prediction_status,
                    pred_result=pred_result, spectrum_qc=qc,
                    save_pred=bool(pred_result and qc["ok"]),
                    pred_source="monitor",
                )
                return

            elif state in _BURST_STATES:
                # 1. Descartar muestras residuales en vuelo emitidas antes del inicio del modo actual
                elapsed_since_start = time.time() - BURST_START_TIME
                if elapsed_since_start < BURST_MIN_INTERVAL_S:
                    print(f"[{state.value}] Descartando muestra residual ({elapsed_since_start*1000:.0f}ms tras inicio)")
                    return

                # QC: en burst, descartar muestras fallidas (no cuentan) — máx N reintentos
                if not qc["ok"]:
                    fails = int(BURST_CONTEXT.get("qc_fails") or 0) + 1
                    BURST_CONTEXT["qc_fails"] = fails
                    print(f"[{state.value}] Muestra QC fail ({fails}/{BURST_MAX_QC_FAILS}): {qc['reasons']}")
                    if fails >= BURST_MAX_QC_FAILS:
                        abort_burst(
                            f"Error: {fails} muestras inválidas seguidas ({qc.get('label')}). "
                            "Revisa cubeta/LEDs/gain e inténtalo de nuevo."
                        )
                        return
                    publish_status({
                        "prediction_status": (
                            f"{state.value}: muestra inválida ({fails}/{BURST_MAX_QC_FAILS}) — reintento"
                        ),
                        "spectrum_qc": qc["level"],
                        "spectrum_qc_label": qc["label"],
                        "system_state": state.value,
                        "burst_count": len(BURST_CONTEXT["current_buffer"]),
                        "burst_target": BURST_CONTEXT["target_count"],
                    })
                    request_next_burst_sample()
                    reset_burst_timer()
                    return

                # 2. Comprobar si ya se ha completado el número requerido
                if len(BURST_CONTEXT["current_buffer"]) >= BURST_CONTEXT["target_count"]:
                    return

                BURST_CONTEXT["current_buffer"].append(raw_data)
                BURST_CONTEXT["qc_fails"] = 0
                count = len(BURST_CONTEXT["current_buffer"])
                target = BURST_CONTEXT["target_count"]
                reset_burst_timer()

                print(f"[{state.value}] Recibiendo muestra {count}/{target}...")
                publish_status({
                    "prediction_status": f"{state.value} ({count}/{target})",
                    "system_state": state.value,
                    "spectrum_qc": qc["level"],
                    "spectrum_qc_label": qc["label"],
                    "burst_count": count,
                    "burst_target": target,
                })

                if count < target:
                    request_next_burst_sample()
                else:
                    cancel_burst_pace_timer()
                    if BURST_TIMER:
                        BURST_TIMER.cancel()
                        BURST_TIMER = None

                    print(f"[{state.value}] Burst completado. Procesando...")
                    burst_buffer = list(BURST_CONTEXT["current_buffer"])
                    burst_label = BURST_CONTEXT.get("label", "Unknown")

                    CURRENT_STATE = SystemState.IDLE
                    BURST_CONTEXT = {"target_count": 0, "current_buffer": [], "label": "Unknown", "qc_fails": 0}
                    client_mosquitto.publish(mqtt_topics.TOPIC_CMD, json.dumps({"mode": "idle"}), qos=1, retain=False)
                    publish_status({"burst_count": target, "burst_target": target})

                    if state == SystemState.BLANK_CHECK:
                        avg_raw = calculate_average_spectrum(burst_buffer) or raw_data
                        check = brain.compare_to_baseline(avg_raw)
                        status = check["label"]
                        print(f"--> Blank check: {check}")
                        send_telemetry(
                            avg_raw, abs_data, brain.baseline is not None,
                            prediction_status=status, spectrum_qc=qc,
                        )
                        publish_status({
                            "blank_check": check.get("level"),
                            "blank_check_label": check.get("label"),
                            "blank_check_ratio": check.get("ratio"),
                            **_decision_fields(),
                        })

                    elif state == SystemState.CALIBRATION:
                        optics = brain.optics_fingerprint(CURRENT_CONFIG)
                        success = brain.calibrate(burst_buffer, optics=optics)
                        if success:
                            status = "Blanco OK — calibración exitosa"
                        else:
                            status = "Error en calibración (muestras inválidas)"
                        print(f"--> {status}")
                        send_telemetry(raw_data, is_calibrated=success, prediction_status=status, spectrum_qc=qc)

                    elif state == SystemState.TRAINING:
                        if is_calibrated:
                            avg_raw = calculate_average_spectrum(burst_buffer)
                            avg_qc = brain.assess_spectrum_qc(avg_raw)
                            _, _, avg_snv = brain.get_absorbance(avg_raw)

                            if avg_snv is None or not avg_qc["ok"]:
                                print("--> Error: Absorbancia/QC del promedio fallida.")
                                send_telemetry(
                                    avg_raw, abs_data, is_calibrated,
                                    prediction_status="Error: Absorbancia o QC fallida",
                                    spectrum_qc=avg_qc,
                                )
                            else:
                                try:
                                    num_val = float(burst_label)
                                except (ValueError, TypeError):
                                    num_val = 0.0
                                    print("--> Aviso: Etiqueta no numerica. Usando 0.0 (solo DEVIATION).")

                                brain.add_training_sample(avg_snv, num_val)
                                print(f"--> Muestra {num_val} guardada. Dataset total: {len(brain.dataset_X)}")
                                brain.save_brain()
                                send_telemetry(
                                    avg_raw, abs_data, is_calibrated,
                                    prediction_status=f"Muestra {num_val} mg/L guardada ({len(brain.dataset_X)} en memoria)",
                                    spectrum_qc=avg_qc,
                                )
                        else:
                            print("--> Error: Falta calibrar.")
                            send_telemetry(raw_data, abs_data, is_calibrated, prediction_status="Error: Falta Calibrar", spectrum_qc=qc)

                    elif state == SystemState.ANALYSIS:
                        if is_calibrated and brain.is_trained:
                            avg_raw = calculate_average_spectrum(burst_buffer)
                            avg_qc = brain.assess_spectrum_qc(avg_raw)
                            _, _, avg_snv = brain.get_absorbance(avg_raw)
                            pred_result = brain.predict_result(avg_snv)
                            print(f"--> Analisis finalizado: {pred_result}")
                            status = "Análisis finalizado"
                            if pred_result and pred_result.get("confidence") == "warn":
                                status = f"Análisis finalizado — {pred_result.get('confidence_label')}"
                            send_telemetry(
                                avg_raw, abs_data, is_calibrated,
                                prediction_status=status,
                                pred_result=pred_result,
                                spectrum_qc=avg_qc,
                                save_pred=True,
                                pred_source="analysis",
                            )
                        else:
                            print("--> Error: Modelo no listo o falta calibracion.")
                            send_telemetry(
                                raw_data, abs_data, is_calibrated,
                                prediction_status="Falta modelo o calibracion",
                                spectrum_qc=qc,
                            )

    except Exception as e:
        print(f"Error en bucle principal MQTT: {e}")

KNOWN_RPC_METHODS = {
    "setIdle", "startFreeMeasure", "calibrate", "startTraining", "startAnalysis",
    "trainModel", "resetModel", "removeTrainingSample",
    "clearBaseline", "checkBlank", "resetAll",
    "setModeDeviation", "setModePLSR", "getModelInfo",
    "saveConfig", "factoryReset", "updateFirmware",
}
# --- PUENTE MQTT (THINGSBOARD -> ESP32) ---
def on_tb_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
        method = data.get("method")
        raw_params = data.get("params")
        rpc_id = msg.topic.split("/")[-1] if "/" in msg.topic else "0"

        if method and "-" in method and isinstance(raw_params, str) and raw_params in KNOWN_RPC_METHODS:
            method = raw_params
            raw_params = {}
            print(f"[RPC] Campos invertidos detectados (controlApi), corrigiendo: method={method}")

        params = parse_rpc_params(raw_params)
        print(f"[RPC] Recibido: {method} | params: {params} | id: {rpc_id}")

        result = commands.execute_command(method, params)
        if result.get("ok"):
            client_tb.publish(f"v1/devices/me/rpc/response/{rpc_id}", json.dumps({"result": result.get("result", "ok"), **{k: v for k, v in result.items() if k not in ("ok", "result", "error")}}))
        else:
            client_tb.publish(f"v1/devices/me/rpc/response/{rpc_id}", json.dumps({"error": result.get("error", "error")}))

    except Exception as e:
        print(f"Error RPC: {e}")


@app.on_event("startup")
async def _capture_loop():
    hub.set_event_loop(asyncio.get_running_loop())


@app.on_event("startup")
def start_bridge():
    print(">>> INICIANDO WATERLY API - STATE MACHINE v4.0 (panel web) <<<")
    print(f"[CFG] TB_ENABLED={TB_ENABLED}")
    _wire_commands()

    if TB_ENABLED:
        tb_ready = False
        for i in range(30):
            if autoconfig_thingsboard():
                print("ThingsBoard configurado.")
                tb_ready = True
                break
            time.sleep(5)
        if not tb_ready:
            print("[WARN] ThingsBoard no disponible; continuo sin autoconfig.")
    else:
        print("[CFG] ThingsBoard deshabilitado (panel web / REST).")

    def on_mosquitto_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            print("[MQTT] Mosquitto conectado")
            client_mosquitto.publish(mqtt_topics.TOPIC_CMD, "", qos=1, retain=True)
            client_mosquitto.subscribe(mqtt_topics.TOPIC_DAT)
            client_mosquitto.subscribe(mqtt_topics.TOPIC_CFG)
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

    try:
        client_mosquitto.connect(MOSQUITTO_HOST, 1883, 60)
        client_mosquitto.loop_start()
    except Exception as e:
        print(f"Error MQTT Mosquitto: {e}")

    if TB_ENABLED:
        client_tb.on_connect = on_tb_connect
        client_tb.on_disconnect = on_tb_disconnect
        client_tb.on_message = on_tb_message
        try:
            client_tb.connect(TB_HOST, 1883, 60)
            client_tb.loop_start()
        except Exception as e:
            print(f"Error MQTT ThingsBoard: {e}")

    publish_status({
        "system_state": CURRENT_STATE.value,
        "calibrated": brain.baseline is not None,
        "blank_ok": brain.baseline is not None,
        "optics_mismatch": brain.optics_mismatch(CURRENT_CONFIG),
        "model_ready": brain.is_trained,
        "model_mode": brain.model_type,
        "prediction_status": "API lista",
    })

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

@app.get("/api/config/current")
def get_current_config():
    if not CURRENT_CONFIG:
        return JSONResponse(status_code=404, content={"error": "ESP32 no ha enviado config aun"})
    return CURRENT_CONFIG

GAIN_LABELS = {0: "1x (bajo)", 1: "3.7x (medio-bajo)", 2: "16x (medio-alto)", 3: "64x (alto)"}
LED_LABELS = {0: "12.5mA (bajo)", 1: "25mA (medio)", 2: "50mA (alto)", 3: "100mA (maximo)"}

def _add_readable_labels(config):
    readable = {}
    if "sensor_gain" in config:
        readable["sensor_gain"] = GAIN_LABELS.get(config["sensor_gain"], f"Desconocido ({config['sensor_gain']})")
    if "sensor_integration" in config:
        ms = config["sensor_integration"] * 2.8
        readable["sensor_integration"] = f"{ms:.0f}ms ({config['sensor_integration']} x 2.8ms)"
    if "sensor_led_current" in config:
        readable["sensor_led_current"] = LED_LABELS.get(config["sensor_led_current"], f"Desconocido ({config['sensor_led_current']})")
    return readable

@app.get("/api/config/download")
def download_config():
    if not CURRENT_CONFIG:
        raise HTTPException(status_code=404, detail="ESP32 no ha enviado config aun. Espera a que se conecte a MQTT.")
    import io
    config_with_labels = dict(CURRENT_CONFIG)
    config_with_labels["_readable"] = _add_readable_labels(CURRENT_CONFIG)
    config_bytes = json.dumps(config_with_labels, indent=2, ensure_ascii=False).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(config_bytes),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=waterly_config.json"}
    )

@app.post("/api/config/upload")
async def upload_config(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .json")
    
    content = await file.read()
    
    try:
        config_data = json.loads(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"JSON invalido: {str(e)}")
    
    valid_keys = {"wifi_ssid", "wifi_pass", "mqtt_broker", "mqtt_topic_cmd", "mqtt_topic_dat",
                  "sensor_gain", "sensor_integration", "sensor_led_current", "ble_pop", "ota_url"}
    
    sent_keys = set(config_data.keys()) & valid_keys
    if not sent_keys:
        raise HTTPException(status_code=400, detail="El JSON no contiene ninguna clave de configuracion valida")
    
    filtered_config = {k: v for k, v in config_data.items() if k in valid_keys}
    
    config_payload = {"config": filtered_config}
    client_mosquitto.publish(mqtt_topics.TOPIC_CMD, json.dumps(config_payload), qos=1, retain=False)
    
    print(f"[CONFIG] Config cargada desde JSON, enviando al ESP32: {list(sent_keys)}")
    
    return {
        "status": "ok",
        "keys_sent": list(sent_keys),
        "message": f"Configuracion enviada al ESP32 ({len(sent_keys)} parametros)"
    }

@app.post("/api/config/factory_reset")
def factory_reset_config():
    reset_payload = {"config": {"factory_reset": True}}
    client_mosquitto.publish(mqtt_topics.TOPIC_CMD, json.dumps(reset_payload), qos=1, retain=False)
    print("[CONFIG] Factory Reset enviado al ESP32 via API directa")
    return {"status": "ok", "message": "Factory Reset enviado al ESP32"}


# =============================================================================
# Panel web: status, comandos REST, WebSocket, historial Influx
# =============================================================================

@app.get("/api/status")
def api_status():
    tel = hub.get_last_telemetry()
    mismatch = brain.optics_mismatch(CURRENT_CONFIG)
    y_min, y_max = brain.train_range()
    if brain.is_trained and brain.metrics:
        y_min = brain.metrics.get("y_min", y_min)
        y_max = brain.metrics.get("y_max", y_max)
    return {
        "status": "Online",
        "state": CURRENT_STATE.value,
        "tb_enabled": TB_ENABLED,
        "telemetry": tel,
        "model": {
            "is_trained": brain.is_trained,
            "model_type": brain.model_type,
            "has_baseline": brain.baseline is not None,
            "n_samples": len(brain.dataset_X),
            "sample_labels": brain.sample_labels(),
            "concentration_counts": brain.concentration_counts(),
            "train_y_min": y_min,
            "train_y_max": y_max,
            "optics_mismatch": mismatch,
            "baseline_optics": brain.baseline_optics,
            "baseline_ts": brain.baseline_ts,
            "rmsecv": (brain.metrics or {}).get("rmsecv"),
            "r2_train": (brain.metrics or {}).get("r2_train"),
            "rpd": (brain.metrics or {}).get("rpd"),
            "models_trained": (brain.metrics or {}).get("models_trained")
            or (list(brain.models.keys()) if getattr(brain, "models", None) else []),
            "ensemble": (brain.metrics or {}).get("ensemble"),
        },
    }


@app.post("/api/cmd/{method}")
async def api_cmd(method: str, body: Optional[dict] = Body(default=None)):
    if method not in KNOWN_RPC_METHODS:
        raise HTTPException(status_code=404, detail=f"Unknown command: {method}")
    result = commands.execute_command(method, body or {})
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "error"))
    return result


@app.get("/api/cmd/getModelInfo")
def api_cmd_get_model_info():
    return commands.execute_command("getModelInfo", {})


@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket):
    await hub.ws_register(websocket)
    try:
        while True:
            # Mantener vivo; el cliente puede enviar ping
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.ws_unregister(websocket)
    except Exception:
        hub.ws_unregister(websocket)


@app.get("/api/history/spectrum")
def api_history_spectrum(minutes: int = 30, kind: str = "raw"):
    """Series temporales desde Influx para el panel (uPlot)."""
    minutes = max(1, min(minutes, 24 * 60))
    measurement = "water_quality_abs" if kind == "abs" else "water_quality_raw"
    wavelengths = brain._get_wavelengths()
    field_names = [f"{w}_abs" for w in wavelengths] if kind == "abs" else [f"raw_{w}" for w in wavelengths]

    try:
        if client_influx is None:
            raise HTTPException(status_code=503, detail="InfluxDB no inicializado")
        query_api = client_influx.query_api()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Influx no disponible: {e}")

    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> filter(fn: (r) => r["device"] == "ESP32_01")
  |> aggregateWindow(every: 2s, fn: mean, createEmpty: false)
  |> yield(name: "mean")
'''
    try:
        tables = query_api.query(flux, org=INFLUX_ORG)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query Influx fallida: {e}")

    series = {name: {"t": [], "v": []} for name in field_names}
    for table in tables:
        for record in table.records:
            field = record.get_field()
            if field not in series:
                continue
            ts = record.get_time()
            if ts is None:
                continue
            try:
                series[field]["t"].append(ts.timestamp())
                series[field]["v"].append(float(record.get_value()))
            except (TypeError, ValueError):
                continue

    # Ordenar cada serie por tiempo
    for name, s in series.items():
        if not s["t"]:
            continue
        paired = sorted(zip(s["t"], s["v"]))
        s["t"] = [p[0] for p in paired]
        s["v"] = [p[1] for p in paired]

    n_points = max((len(s["t"]) for s in series.values()), default=0)
    return {"kind": kind, "minutes": minutes, "n_points": n_points, "series": series}


@app.get("/api/history/predictions")
def api_history_predictions(minutes: int = 60):
    """Historial de resultados de concentración / desviación."""
    minutes = max(1, min(minutes, 24 * 60))
    try:
        if client_influx is None:
            raise HTTPException(status_code=503, detail="InfluxDB no inicializado")
        query_api = client_influx.query_api()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Influx no disponible: {e}")

    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r["_measurement"] == "water_quality_pred")
  |> filter(fn: (r) => r["device"] == "ESP32_01")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 500)
'''
    try:
        tables = query_api.query(flux, org=INFLUX_ORG)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query Influx fallida: {e}")

    rows = []
    for table in tables:
        for record in table.records:
            values = record.values
            ts = record.get_time()
            rows.append({
                "t": ts.timestamp() if ts else None,
                "iso": ts.isoformat() if ts else None,
                "value": values.get("value"),
                "pred_mg_l": values.get("pred_mg_l"),
                "pred_deviation": values.get("pred_deviation"),
                "t2": values.get("t2"),
                "q": values.get("q"),
                "kind": values.get("kind"),
                "source": values.get("source"),
                "confidence": values.get("confidence"),
                "qc": values.get("qc"),
                "mode": values.get("mode"),
            })

    return {"minutes": minutes, "n_points": len(rows), "rows": rows}


@app.get("/api/history/predictions.csv")
def api_history_predictions_csv(minutes: int = 60):
    import csv
    import io
    data = api_history_predictions(minutes)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["iso", "t", "value", "pred_mg_l", "pred_deviation", "kind", "mode", "source", "confidence", "qc", "t2", "q"],
    )
    writer.writeheader()
    for row in data["rows"]:
        writer.writerow({k: row.get(k) for k in writer.fieldnames})
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=waterly_predictions_{minutes}m.csv"},
    )
