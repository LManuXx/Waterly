"""
Comandos de operación Waterly (paridad con RPC ThingsBoard).
Usados por REST /api/cmd/* y por el puente MQTT de ThingsBoard.
"""
import json
import os
import threading
import time
from typing import Any, Dict


import mqtt_topics


class CommandContext:
    """Referencias mutables al estado de main.py."""

    def __init__(self):
        self.SystemState = None
        self.state_lock = None
        self.get_state = None
        self.set_state = None
        self.get_burst_context = None
        self.set_burst_context = None
        self.get_burst_timer = None
        self.set_burst_timer = None
        self.set_burst_start = None
        self.reset_burst_timer = None
        self.cancel_burst_pace_timer = None
        self.request_next_burst_sample = None
        self.client_mosquitto = None
        self.brain = None
        self.publish_status = None
        self.FIRMWARE_BIN = None
        self.FIRMWARE_VERSION_JSON = None
        self.get_current_config = None


ctx = CommandContext()


def _cancel_burst_timer():
    timer = ctx.get_burst_timer()
    if timer is not None:
        timer.cancel()
        ctx.set_burst_timer(None)
    cancel = getattr(ctx, "cancel_burst_pace_timer", None)
    if callable(cancel):
        cancel()


def _clamp_samples(n: int) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(50, n))


def _clear_prediction_fields():
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


def _begin_burst(state, samples: int, label: str = "Unknown", status_msg: str = None):
    """Para monitor continuo, arma el burst y pide la 1ª muestra tras un pequeño delay."""
    samples = _clamp_samples(samples)
    _cancel_burst_timer()
    # Parar monitor continuo / medida en curso en el ESP
    ctx.client_mosquitto.publish(mqtt_topics.TOPIC_CMD, json.dumps({"mode": "idle"}), qos=1, retain=False)
    with ctx.state_lock:
        ctx.set_state(state)
        ctx.set_burst_start(time.time())
        ctx.set_burst_context({
            "target_count": samples,
            "current_buffer": [],
            "label": label,
            "qc_fails": 0,
        })
        ctx.reset_burst_timer()
    msg = status_msg or f"{state.value} (0/{samples})"
    ctx.publish_status({
        "prediction_status": msg,
        "system_state": state.value,
        "burst_count": 0,
        "burst_target": samples,
        **_clear_prediction_fields(),
    })
    # Primera lectura tras BURST_PACE: deja que residuos del monitor caigan en IDLE
    req = getattr(ctx, "request_next_burst_sample", None)
    if callable(req):
        req()
    else:
        ctx.client_mosquitto.publish(mqtt_topics.TOPIC_CMD, json.dumps({"mode": "single"}), qos=1, retain=False)
    return samples


def execute_command(method: str, params: Any = None) -> Dict[str, Any]:
    """Ejecuta un comando. Devuelve {ok, result|error, ...}."""
    params = params if params is not None else {}

    if method == "setIdle":
        with ctx.state_lock:
            ctx.set_state(ctx.SystemState.IDLE)
            ctx.set_burst_context({"target_count": 0, "current_buffer": [], "label": "Unknown", "qc_fails": 0})
            _cancel_burst_timer()
        ctx.client_mosquitto.publish(mqtt_topics.TOPIC_CMD, json.dumps({"mode": "idle"}), qos=1, retain=False)
        ctx.publish_status({
            "prediction_status": "Forzado a Reposo",
            "system_state": "IDLE",
            "burst_count": 0,
            "burst_target": 0,
        })
        return {"ok": True, "result": "ok"}

    if method == "startFreeMeasure":
        with ctx.state_lock:
            ctx.set_state(ctx.SystemState.FREE_MEASURE)
            _cancel_burst_timer()
        ctx.client_mosquitto.publish(mqtt_topics.TOPIC_CMD, json.dumps({"mode": "training"}), qos=1, retain=False)
        ctx.publish_status({
            "prediction_status": "Monitorizando",
            "system_state": "FREE_MEASURE",
            "burst_count": 0,
            "burst_target": 0,
        })
        return {"ok": True, "result": "ok"}

    if method == "calibrate":
        samples = 5
        if isinstance(params, dict):
            samples = params.get("samples", 5)
        elif isinstance(params, (int, float)):
            samples = params
        samples = _begin_burst(
            ctx.SystemState.CALIBRATION,
            samples,
            status_msg=None,
        )
        # _begin_burst already set status; fix message
        ctx.publish_status({
            "prediction_status": f"CALIBRATION (0/{samples})",
            "system_state": "CALIBRATION",
            "burst_count": 0,
            "burst_target": samples,
        })
        return {"ok": True, "result": "ok", "samples": samples}

    if method == "startTraining":
        label = None
        samples = 5
        if isinstance(params, dict):
            label = params.get("label")
            samples = params.get("samples", 5)
        elif params is not None and not isinstance(params, (dict, list)):
            label = params

        if ctx.brain.baseline is None:
            ctx.publish_status({"prediction_status": "Error: Falta calibrar antes de entrenar."})
            return {"ok": False, "error": "Falta calibrar"}

        cfg = ctx.get_current_config() if ctx.get_current_config else None
        if ctx.brain.optics_mismatch(cfg or {}):
            ctx.publish_status({
                "prediction_status": "Error: Óptica distinta al blanco. Recalibra con agua limpia.",
                "optics_mismatch": True,
            })
            return {"ok": False, "error": "Óptica distinta al blanco — recalibra"}

        if ctx.brain.model_type == "PLSR":
            try:
                num_label = float(str(label).strip())
            except (TypeError, ValueError):
                ctx.publish_status({
                    "prediction_status": "Error: En modo concentración (PLSR) el label debe ser un número (mg/L).",
                })
                return {"ok": False, "error": "Label inválido para PLSR (usa mg/L numérico)"}
            if num_label < 0:
                return {"ok": False, "error": "Label mg/L no puede ser negativo"}
            label = str(num_label)
        else:
            label = str(label if label is not None else "0.0")

        samples = _begin_burst(
            ctx.SystemState.TRAINING,
            samples,
            label=label,
        )
        ctx.publish_status({
            "prediction_status": f"TRAINING (0/{samples}) - Muestra {label} mg/L",
            "system_state": "TRAINING",
            "burst_count": 0,
            "burst_target": samples,
        })
        return {"ok": True, "result": "ok", "label": label, "samples": samples}

    if method == "startAnalysis":
        if not ctx.brain.is_trained:
            ctx.publish_status({
                "prediction_status": "Error: Modelo no entrenado. Primero haz trainModel.",
                **_clear_prediction_fields(),
            })
            return {"ok": False, "error": "Modelo no entrenado"}
        if ctx.brain.baseline is None:
            ctx.publish_status({
                "prediction_status": "Error: Falta calibrar (baseline no establecida).",
                **_clear_prediction_fields(),
            })
            return {"ok": False, "error": "Falta calibrar"}
        cfg = ctx.get_current_config() if ctx.get_current_config else None
        if ctx.brain.optics_mismatch(cfg or {}):
            ctx.publish_status({
                "prediction_status": "Error: Óptica distinta al blanco. Recalibra antes de analizar.",
                "optics_mismatch": True,
                **_clear_prediction_fields(),
            })
            return {"ok": False, "error": "Óptica distinta al blanco — recalibra"}

        samples = 5
        if isinstance(params, dict):
            samples = params.get("samples", 5)
        elif isinstance(params, (int, float)):
            samples = params
        samples = _begin_burst(ctx.SystemState.ANALYSIS, samples)
        ctx.publish_status({
            "prediction_status": f"ANALYSIS (0/{samples})",
            "system_state": "ANALYSIS",
            "burst_count": 0,
            "burst_target": samples,
            **_clear_prediction_fields(),
        })
        return {"ok": True, "result": "ok", "samples": samples}

    if method == "trainModel":
        with ctx.state_lock:
            st = ctx.get_state()
            if st in [
                ctx.SystemState.CALIBRATION,
                ctx.SystemState.TRAINING,
                ctx.SystemState.ANALYSIS,
                ctx.SystemState.BLANK_CHECK,
            ]:
                msg = f"Burst activo ({st.value}). Pulsa IDLE y espera."
                ctx.publish_status({"prediction_status": msg})
                return {"ok": False, "error": msg}

        if ctx.brain.model_type == "PLSR" and len(ctx.brain.dataset_X) < 3:
            msg = f"PLSR necesita ≥3 muestras (tienes {len(ctx.brain.dataset_X)})."
            ctx.publish_status({"prediction_status": msg})
            return {"ok": False, "error": msg}

        def _train():
            n_samples = len(ctx.brain.dataset_X)
            print(f"[CMD] Entrenando modelo ({ctx.brain.model_type}) con {n_samples} muestras...")
            ctx.publish_status({
                "prediction_status": f"Entrenando modelo {ctx.brain.model_type} con {n_samples} muestras...",
                **_clear_prediction_fields(),
            })
            success = ctx.brain.train_model()
            if success:
                metrics = ctx.brain.get_model_info()
                if ctx.brain.model_type == "DEVIATION":
                    summary = (
                        f"ENTRENAMIENTO COMPLETADO | Modo alerta (Deviation) | "
                        f"{metrics.get('n_samples', '?')} muestras"
                    )
                    extra = {"model_metrics_rmsecv": None}
                else:
                    r2 = metrics.get("r2_train", 0)
                    rmsecv = metrics.get("rmsecv", 0)
                    n_comp = metrics.get("n_components", 0)
                    models = metrics.get("models_trained") or list((metrics.get("ensemble") or {}).keys()) or ["PLSR"]
                    models_txt = "+".join(models)
                    rpd = metrics.get("rpd")
                    rpd_txt = f" | RPD={rpd:.2f}" if isinstance(rpd, (int, float)) else ""
                    summary = (
                        f"ENTRENAMIENTO COMPLETADO | Ensemble [{models_txt}] | {n_samples} muestras | "
                        f"PLSR {n_comp} comp | R²={r2:.2f} | RMSECV={rmsecv:.1f} mg/L{rpd_txt}"
                    )
                    extra = {
                        "model_metrics_rmsecv": rmsecv,
                        "model_metrics_r2": r2,
                        "models_trained": models,
                    }
                ctx.publish_status({
                    "prediction_status": summary,
                    "model_ready": True,
                    "model_mode": ctx.brain.model_type,
                    **_clear_prediction_fields(),
                    **extra,
                })
            else:
                ctx.publish_status({
                    "prediction_status": f"ERROR ENTRENAMIENTO | Muestras: {n_samples} | Mira logs del API"
                })
            print(f"[CMD] ML Resultado: {'OK' if success else 'Error'}")

        threading.Thread(target=_train, daemon=True).start()
        return {"ok": True, "result": "Entrenamiento iniciado"}

    if method == "resetModel":
        ctx.brain.reset_model()
        ctx.publish_status({
            "prediction_status": "Modelo Borrado",
            "model_ready": False,
            "model_mode": ctx.brain.model_type,
            "n_samples": 0,
            "sample_labels": [],
            "concentration_counts": {},
            "train_y_min": None,
            "train_y_max": None,
            **_clear_prediction_fields(),
        })
        return {"ok": True, "result": "Modelo Borrado"}

    if method == "clearBaseline":
        ctx.brain.clear_baseline()
        ctx.publish_status({
            "prediction_status": "Blanco borrado. Calibra con agua limpia antes de medir.",
            "calibrated": False,
            "blank_ok": False,
            "has_baseline": False,
            "baseline_ts": None,
            "optics_mismatch": False,
            "blank_check": None,
            "blank_check_label": None,
            "blank_check_ratio": None,
        })
        return {"ok": True, "result": "Blanco borrado"}

    if method == "checkBlank":
        if ctx.brain.baseline is None:
            ctx.publish_status({"prediction_status": "Error: No hay blanco. Calibra primero."})
            return {"ok": False, "error": "No hay blanco"}
        samples = _begin_burst(ctx.SystemState.BLANK_CHECK, 1)
        ctx.publish_status({
            "prediction_status": f"BLANK_CHECK (0/{samples})",
            "system_state": "BLANK_CHECK",
            "burst_count": 0,
            "burst_target": samples,
            "blank_check": None,
            "blank_check_label": "Comprobando…",
        })
        return {"ok": True, "result": "ok", "samples": samples}

    if method == "resetAll":
        ctx.brain.clear_baseline()
        ctx.brain.reset_model()
        ctx.publish_status({
            "prediction_status": "Sesión reiniciada: blanco, muestras y modelo borrados.",
            "calibrated": False,
            "blank_ok": False,
            "has_baseline": False,
            "model_ready": False,
            "n_samples": 0,
            "sample_labels": [],
            "concentration_counts": {},
            "train_y_min": None,
            "train_y_max": None,
            "optics_mismatch": False,
            "blank_check": None,
            "blank_check_label": None,
            "blank_check_ratio": None,
            **_clear_prediction_fields(),
        })
        return {"ok": True, "result": "Todo borrado"}

    if method == "removeTrainingSample":
        idx = None
        if isinstance(params, dict):
            idx = params.get("index")
        if idx is None:
            # Deshacer última muestra
            if len(ctx.brain.dataset_X) == 0:
                return {"ok": False, "error": "No hay muestras"}
            idx = len(ctx.brain.dataset_X) - 1
        ok = ctx.brain.remove_training_sample(int(idx))
        if not ok:
            return {"ok": False, "error": "Índice inválido"}
        y_min, y_max = ctx.brain.train_range()
        ctx.publish_status({
            "prediction_status": f"Muestra {idx} eliminada. Reentrena el modelo.",
            "model_ready": False,
            "n_samples": len(ctx.brain.dataset_X),
            "sample_labels": ctx.brain.sample_labels(),
            "concentration_counts": ctx.brain.concentration_counts(),
            "train_y_min": y_min,
            "train_y_max": y_max,
        })
        return {"ok": True, "result": "ok", "n_samples": len(ctx.brain.dataset_X)}

    if method == "setModeDeviation":
        ctx.brain.model_type = "DEVIATION"
        ctx.brain.save_brain()
        ctx.publish_status({
            "prediction_status": "Modo: alerta (Deviation) — no es concentración mg/L",
            "model_mode": "DEVIATION",
            **_clear_prediction_fields(),
        })
        return {"ok": True, "result": "Modo cambiado a DEVIATION"}

    if method == "setModePLSR":
        ctx.brain.model_type = "PLSR"
        ctx.brain.save_brain()
        ctx.publish_status({
            "prediction_status": "Modo: concentración (PLSR) — resultado en mg/L",
            "model_mode": "PLSR",
            **_clear_prediction_fields(),
        })
        return {"ok": True, "result": "Modo cambiado a PLSR"}

    if method == "getModelInfo":
        info = ctx.brain.get_model_info()
        return {"ok": True, "result": "ok", "info": info}

    if method == "saveConfig":
        if not params or not isinstance(params, dict):
            return {"ok": False, "error": "no params"}
        ctx.client_mosquitto.publish(
            mqtt_topics.TOPIC_CMD, json.dumps({"config": params}), qos=1, retain=False
        )
        optics_keys = {"sensor_gain", "sensor_integration", "sensor_led_current"}
        if optics_keys & set(params.keys()) and ctx.brain.baseline is not None:
            ctx.publish_status({
                "prediction_status": "Config óptica enviada. Recalibra el blanco tras reiniciar el ESP32.",
                "optics_mismatch": True,
            })
        else:
            ctx.publish_status({"prediction_status": "Configuracion enviada al ESP32. Reiniciando..."})
        return {"ok": True, "result": "Config enviada al ESP32. Reiniciando..."}

    if method == "factoryReset":
        ctx.client_mosquitto.publish(
            mqtt_topics.TOPIC_CMD,
            json.dumps({"config": {"factory_reset": True}}),
            qos=1,
            retain=False,
        )
        ctx.publish_status({"prediction_status": "Factory Reset enviado al ESP32"})
        return {"ok": True, "result": "Factory reset enviado"}

    if method == "updateFirmware":
        if os.path.exists(ctx.FIRMWARE_BIN) and os.path.exists(ctx.FIRMWARE_VERSION_JSON):
            with open(ctx.FIRMWARE_VERSION_JSON, "r") as f:
                ver_data = json.load(f)
            ctx.client_mosquitto.publish(
                mqtt_topics.TOPIC_CMD, json.dumps({"update": True}), qos=1, retain=False
            )
            ctx.publish_status({
                "prediction_status": f"OTA iniciada - version {ver_data['version']}"
            })
            return {"ok": True, "result": f"Update iniciado (v{ver_data['version']})"}
        return {"ok": False, "error": "no firmware uploaded"}

    return {"ok": False, "error": f"Unknown method: {method}"}
