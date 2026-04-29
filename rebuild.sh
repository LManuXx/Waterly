#!/bin/bash
# rebuild.sh - Script para parar, recompilar y arrancar los servicios de Waterly

# Navegar al directorio donde se encuentra el docker-compose.yml
cd "$(dirname "$0")/waterly_server" || { echo "Error: No se encontró el directorio waterly_server"; exit 1; }

echo "🛑 Deteniendo los contenedores actuales..."
docker compose down

echo "🔨 Recompilando la API de Python y arrancando todos los servicios..."
docker compose up -d --build

echo "✅ ¡Listo! Servicios actualizados y corriendo en segundo plano."
echo "Puedes ver los logs en vivo con: cd waterly_server && docker compose logs -f"
