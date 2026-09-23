"""
Hub de telemetría: última muestra en memoria + broadcast WebSocket + opcional ThingsBoard.
"""
import asyncio
import json
import threading
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket

_lock = threading.RLock()
LAST_TELEMETRY: Dict[str, Any] = {
    "system_state": "IDLE",
    "calibrated": False,
    "prediction_status": "Arranque",
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
    "model_mode": "PLSR",
    "model_ready": False,
    "n_samples": 0,
    "sample_labels": [],
    "concentration_counts": {},
    "train_y_min": None,
    "train_y_max": None,
    "has_baseline": False,
    "baseline_ts": None,
    "blank_check": None,
    "blank_check_label": None,
    "blank_check_ratio": None,
    "burst_count": 0,
    "burst_target": 0,
    "spectrum_qc": None,
    "spectrum_qc_label": None,
    "optics_mismatch": False,
    "blank_ok": False,
}


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


_ws_clients: Set[WebSocket] = set()
_loop: Optional[asyncio.AbstractEventLoop] = None


def get_last_telemetry() -> Dict[str, Any]:
    with _lock:
        return dict(LAST_TELEMETRY)


def update_telemetry(partial: Dict[str, Any], client_tb=None, tb_enabled: bool = False) -> Dict[str, Any]:
    """Fusiona partial en LAST_TELEMETRY, notifica WS y opcionalmente TB."""
    with _lock:
        LAST_TELEMETRY.update(partial)
        snapshot = dict(LAST_TELEMETRY)

    if tb_enabled and client_tb is not None:
        try:
            client_tb.publish("v1/devices/me/telemetry", json.dumps(partial), qos=0)
        except Exception as e:
            print(f"[HUB] Error publish TB: {e}")

    _broadcast_ws(snapshot)
    return snapshot


def _broadcast_ws(snapshot: Dict[str, Any]) -> None:
    if not _ws_clients or _loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(_broadcast_async(snapshot), _loop)
    except Exception as e:
        print(f"[HUB] Error schedule WS broadcast: {e}")


async def _broadcast_async(snapshot: Dict[str, Any]) -> None:
    dead = []
    payload = json.dumps(snapshot, default=str)
    for ws in list(_ws_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


async def ws_register(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.add(ws)
    await ws.send_text(json.dumps(get_last_telemetry(), default=str))


def ws_unregister(ws: WebSocket) -> None:
    _ws_clients.discard(ws)
