#!/bin/bash
# stop.sh - Script para detener los servicios de Waterly

# Navegar al directorio donde se encuentra el docker-compose.yml
cd "$(dirname "$0")/waterly_server" || { echo "Error: No se encontró el directorio waterly_server"; exit 1; }

# Detectar comando de docker compose disponible
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
else
    DC="docker-compose"
fi

echo "Deteniendo los contenedores de Waterly..."
$DC down

echo "✅ Servicios detenidos correctamente."
