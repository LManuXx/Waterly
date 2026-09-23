"""Topics MQTT compartidos entre API y ESP (defaults Waterly)."""
import os
from typing import Optional

TOPIC_CMD = os.getenv("MQTT_TOPIC_CMD", "waterly/comandos")
TOPIC_DAT = os.getenv("MQTT_TOPIC_DAT", "waterly/datos")
TOPIC_CFG = os.getenv("MQTT_TOPIC_CFG", "waterly/config")


def set_topics(cmd: Optional[str] = None, dat: Optional[str] = None, cfg: Optional[str] = None) -> None:
    global TOPIC_CMD, TOPIC_DAT, TOPIC_CFG
    if cmd and isinstance(cmd, str) and cmd.strip():
        TOPIC_CMD = cmd.strip()
    if dat and isinstance(dat, str) and dat.strip():
        TOPIC_DAT = dat.strip()
    if cfg and isinstance(cfg, str) and cfg.strip():
        TOPIC_CFG = cfg.strip()


def apply_from_esp_config(config: dict) -> None:
    """Si el ESP publica topics distintos, el API sigue su lead."""
    if not isinstance(config, dict):
        return
    set_topics(
        cmd=config.get("mqtt_topic_cmd"),
        dat=config.get("mqtt_topic_dat"),
    )
