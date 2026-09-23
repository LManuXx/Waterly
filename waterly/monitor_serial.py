#!/usr/bin/env python3
"""
Monitor serie del ESP32 sin forzar DOWNLOAD_BOOT ni quedarse en 'waiting for download'.

Problema típico CH340 + placa custom:
  Abrir el puerto con DTR/RTS por defecto puede dejar GPIO0 en LOW → bootloader
  esperando flash en lugar de ejecutar la app.

Este script:
  - Abre el puerto con DTR=0 y RTS=0 (no reset, no boot-download)
  - Solo imprime lo que el ESP escribe a 115200
  - Ctrl+C para salir (deja el ESP corriendo)

Uso:
  ./monitor.sh                    # solo escuchar (recomendado)
  ./monitor.sh --reset            # reset limpio a app y luego escuchar
  ./monitor.sh /dev/ttyUSB1
  ./monitor.sh --reset /dev/ttyUSB0
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("ERROR: pyserial no disponible.")
    print("  source ~/esp-idf-v5.5.1/export.sh")
    print("  # o: pip install pyserial")
    sys.exit(1)


def clean_boot(port: str) -> None:
    """Mismo protocolo que post_flash_boot.py: app boot, no download."""
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 0.2
    ser.dtr = False
    ser.rts = False
    ser.open()
    ser.dtr = False
    ser.rts = True
    time.sleep(0.1)
    ser.rts = False
    time.sleep(0.15)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.05)
    ser.close()
    print(f"[monitor] Reset limpio → app en {port}")


def open_monitor(port: str) -> serial.Serial:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 0.2
    # CRÍTICO: fijar antes de open() para que CH340 no tire BOOT/EN
    ser.dtr = False
    ser.rts = False
    ser.open()
    # Por si el driver las tocó al abrir
    ser.dtr = False
    ser.rts = False
    return ser


def main() -> int:
    ap = argparse.ArgumentParser(description="Monitor ESP32 sin DOWNLOAD_BOOT")
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0", help="Puerto serie")
    ap.add_argument(
        "--reset",
        action="store_true",
        help="Pulso EN limpio (arranca app) antes de escuchar",
    )
    args = ap.parse_args()

    if args.reset:
        try:
            clean_boot(args.port)
            time.sleep(0.3)
        except serial.SerialException as e:
            print(f"ERROR reset: {e}")
            return 1

    try:
        ser = open_monitor(args.port)
    except serial.SerialException as e:
        print(f"ERROR abriendo {args.port}: {e}")
        print("  ¿Está enchufado? ¿Otro proceso usa el puerto (idf monitor)?")
        return 1

    print(f"[monitor] Escuchando {args.port} @ 115200  (DTR=0 RTS=0, sin flash)")
    print("[monitor] Ctrl+C para salir — el ESP sigue en marcha\n")

    warn_download = False
    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            try:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            except Exception:
                continue
            print(line, flush=True)
            low = line.lower()
            if not warn_download and (
                "waiting for download" in low
                or "download_boot" in low
                or "boot:0x3" in low
            ):
                warn_download = True
                print(
                    "\n[monitor] ¡Parece DOWNLOAD_BOOT! Sal (Ctrl+C) y ejecuta:\n"
                    f"  python3 post_flash_boot.py {args.port}\n"
                    "  ./monitor.sh\n",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("\n[monitor] Cerrado.")
    finally:
        try:
            ser.dtr = False
            ser.rts = False
            ser.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
