import socket
import time
import signal
import sys

def get_local_ip():
    """Obtiene la IP real del host (no Docker)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip

def main():
    from zeroconf import Zeroconf, ServiceInfo

    ip = get_local_ip()
    print(f"[mDNS] IP detectada: {ip}")
    print(f"[mDNS] Publicando 'waterly.local' -> {ip}")

    zc = Zeroconf()

    # 1. Publicar servicio MQTT (para descubrimiento por servicio)
    mqtt_info = ServiceInfo(
        "_mqtt._tcp.local.",
        "waterly._mqtt._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=1883,
        properties={"path": "/waterly"},
        server="waterly.local.",
    )
    zc.register_service(mqtt_info)

    # 2. Publicar servicio HTTP (para que mdns_query_a("waterly") funcione)
    host_info = ServiceInfo(
        "_http._tcp.local.",
        "waterly._http._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=8000,
        server="waterly.local.",
    )
    zc.register_service(host_info)

    print(f"[mDNS] Servicios registrados. Esperando consultas...")

    # Mantener vivo y cerrar limpiamente
    def shutdown(sig, frame):
        print("[mDNS] Apagando...")
        zc.unregister_service(mqtt_info)
        zc.unregister_service(host_info)
        zc.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
