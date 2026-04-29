#!/bin/bash
# stop.sh - Script para detener los servicios de Waterly

# Navegar al directorio donde se encuentra el docker-compose.yml
cd "$(dirname "$0")/waterly_server" || { echo "Error: No se encontró el directorio waterly_server"; exit 1; }

echo "Deteniendo los servicios de Docker..."
docker compose down

echo "🛑 Servicios detenidos correctamente."
