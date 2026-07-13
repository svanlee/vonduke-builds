#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  Start Mastermind — hive coordinator + local broker    ║
# ║                                                        ║
# ║  1. Brings up mosquitto (local MQTT broker) if it's    ║
# ║     not already listening on MQTT_PORT.                ║
# ║  2. Starts mastermind/coordinator.py against it.        ║
# ╚══════════════════════════════════════════════════════╝

set -euo pipefail

AKSUMAEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$AKSUMAEL_DIR/venv/bin/python3"
MQTT_HOST="${MQTT_HOST:-127.0.0.1}"
MQTT_PORT="${MQTT_PORT:-1883}"
MOSQUITTO_CONF="/tmp/aksumael_mosquitto.conf"
MOSQUITTO_LOG="/tmp/aksumael_mosquitto.log"

echo "[START_MASTERMIND] AKSUMAEL dir: $AKSUMAEL_DIR"

broker_is_up() {
    (exec 3<>"/dev/tcp/$MQTT_HOST/$MQTT_PORT") 2>/dev/null && exec 3<&- 3>&-
}

if broker_is_up; then
    echo "[START_MASTERMIND] MQTT broker already listening on $MQTT_HOST:$MQTT_PORT — reusing it."
else
    if ! command -v mosquitto >/dev/null 2>&1; then
        echo "[START_MASTERMIND] mosquitto is not installed and nothing is listening on"
        echo "  $MQTT_HOST:$MQTT_PORT. Install it with:"
        echo "    sudo apt-get update && sudo apt-get install -y mosquitto mosquitto-clients"
        echo "  or point MQTT_HOST/MQTT_PORT at a broker that's already running elsewhere"
        echo "  on the WiFi mesh, e.g.: MQTT_HOST=192.168.1.156 tools/start_mastermind.sh"
        exit 1
    fi
    echo "[START_MASTERMIND] starting local mosquitto broker on port $MQTT_PORT..."
    cat > "$MOSQUITTO_CONF" <<EOF
listener $MQTT_PORT 0.0.0.0
allow_anonymous true
log_dest file $MOSQUITTO_LOG
EOF
    mosquitto -c "$MOSQUITTO_CONF" -d
    sleep 1
    if ! broker_is_up; then
        echo "[START_MASTERMIND] mosquitto failed to come up — check $MOSQUITTO_LOG"
        exit 1
    fi
    echo "[START_MASTERMIND] mosquitto up (log: $MOSQUITTO_LOG)"
fi

if ! "$VENV_PYTHON" -c "import paho.mqtt.client" 2>/dev/null; then
    echo "[START_MASTERMIND] paho-mqtt not installed in venv — installing..."
    "$VENV_PYTHON" -m pip install paho-mqtt
fi

echo "[START_MASTERMIND] starting coordinator (host=$MQTT_HOST port=$MQTT_PORT)..."
cd "$AKSUMAEL_DIR"
exec "$VENV_PYTHON" -u -m mastermind.coordinator --host "$MQTT_HOST" --port "$MQTT_PORT"
