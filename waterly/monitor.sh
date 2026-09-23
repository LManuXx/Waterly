#!/bin/bash
# Monitor serie sin meter el ESP en DOWNLOAD_BOOT ni bloquearse en flash.
# Usa pyserial con DTR/RTS a 0 (no reset al abrir).
#
# Uso:
#   ./monitor.sh              # solo logs
#   ./monitor.sh --reset      # reset limpio a app + logs
#   ./monitor.sh /dev/ttyUSB1
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="/dev/ttyUSB0"
RESET=0
ARGS=()

for a in "$@"; do
  case "$a" in
    --reset) RESET=1 ;;
    /dev/*) PORT="$a" ;;
    -h|--help)
      echo "Uso: $0 [--reset] [/dev/ttyUSB0]"
      exit 0
      ;;
    *) ARGS+=("$a") ;;
  esac
done

if [[ -n "${IDF_PYTHON_ENV_PATH:-}" && -x "${IDF_PYTHON_ENV_PATH}/bin/python" ]]; then
  PY="${IDF_PYTHON_ENV_PATH}/bin/python"
elif [[ -x "$HOME/.espressif/python_env/idf5.5_py3.12_env/bin/python" ]]; then
  PY="$HOME/.espressif/python_env/idf5.5_py3.12_env/bin/python"
elif [[ -x "$HOME/esp-idf-v5.5.1/export.sh" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/esp-idf-v5.5.1/export.sh" >/dev/null
  PY="${IDF_PYTHON_ENV_PATH}/bin/python"
else
  PY=python3
fi

CMD=("$PY" "$SCRIPT_DIR/monitor_serial.py" "$PORT")
if [[ "$RESET" -eq 1 ]]; then
  CMD+=(--reset)
fi
CMD+=("${ARGS[@]}")

exec "${CMD[@]}"
