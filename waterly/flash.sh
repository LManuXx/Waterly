#!/bin/bash
# Flash + reset limpio (evita quedarse en DOWNLOAD_BOOT / waiting for download)
set -e
PORT="${1:-/dev/ttyUSB0}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Flash con hard_reset de esptool; luego post_flash_boot corrige DTR/RTS (CH340)
idf.py -p "$PORT" flash

# Preferir pyserial del entorno ESP-IDF
if [[ -n "${IDF_PYTHON_ENV_PATH:-}" && -x "${IDF_PYTHON_ENV_PATH}/bin/python" ]]; then
  PY="${IDF_PYTHON_ENV_PATH}/bin/python"
elif [[ -x "$HOME/.espressif/python_env/idf5.5_py3.12_env/bin/python" ]]; then
  PY="$HOME/.espressif/python_env/idf5.5_py3.12_env/bin/python"
else
  PY=python3
fi

"$PY" "$SCRIPT_DIR/post_flash_boot.py" "$PORT"
