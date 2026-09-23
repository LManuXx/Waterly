#!/usr/bin/env python3
"""
Reset limpio tras flashear: libera DTR (GPIO0/BOOT en HIGH) y pulsa RTS (EN).

En placas custom con CH340 + Q1/Q2, el hard_reset de esptool a veces deja
DTR activo → GPIO0 en LOW al salir de reset → boot:0x3 DOWNLOAD_BOOT
("waiting for download"). Este script fuerza arranque normal en app.

Uso:
  python3 post_flash_boot.py [/dev/ttyUSB0]
"""
import sys
import time

try:
    import serial
except ImportError:
    print("ERROR: pyserial no disponible. Activa ESP-IDF o: pip install pyserial")
    sys.exit(1)

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"


def boot_to_app(port: str) -> None:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 0.5
    # Abrir sin tocar líneas todavía
    ser.dtr = False
    ser.rts = False
    ser.open()

    # 1) Asegurar GPIO0 HIGH (no download) y EN LOW (reset)
    ser.dtr = False
    ser.rts = True
    time.sleep(0.1)

    # 2) Liberar EN con GPIO0 aún HIGH → boot normal a la app
    ser.rts = False
    time.sleep(0.15)

    # 3) Dejar ambas líneas inactivas (pull-ups de la placa)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.05)
    ser.close()
    print(f"[post_flash_boot] Reset limpio en {port} (DTR=0, RTS pulse). Debería arrancar la app.")


if __name__ == "__main__":
    try:
        boot_to_app(PORT)
    except serial.SerialException as e:
        print(f"ERROR abriendo {PORT}: {e}")
        sys.exit(1)
