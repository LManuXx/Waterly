#!/bin/bash
# start.sh - Arranca Waterly (API + Mosquitto + Influx + panel web). ThingsBoard NO por defecto.

cd "$(dirname "$0")/waterly_server" || { echo "Error: No se encontró waterly_server"; exit 1; }

if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
else
    DC="docker-compose"
fi

echo "Iniciando servicios con $DC (Mosquitto, InfluxDB, API, panel web, mDNS)..."
echo "Panel: http://localhost:3000  |  API: http://localhost:8000"
echo "ThingsBoard (opt-in): COMPOSE_PROFILES=thingsboard TB_ENABLED=true $DC up -d"
$DC up -d --build

echo "Servicios desplegados. Logs: cd waterly_server && $DC logs -f"
