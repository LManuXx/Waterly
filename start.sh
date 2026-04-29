#!/bin/bash
# start.sh - Script para arrancar los servicios de Waterly

# Navegar al directorio donde se encuentra el docker-compose.yml
cd "$(dirname "$0")/waterly_server" || { echo "Error: No se encontró el directorio waterly_server"; exit 1; }

echo "Iniciando los servicios de Docker (Backend, Mosquitto, ThingsBoard, PostgreSQL)..."
docker compose up -d

echo "✅ Servicios desplegados correctamente."
echo "Puedes ver los logs con: docker compose logs -f"
