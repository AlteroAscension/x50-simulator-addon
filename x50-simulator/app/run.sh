#!/usr/bin/env bash

CONFIG_PATH="/data/options.json"

if [ -f "$CONFIG_PATH" ]; then
    ADB_ENABLED=$(jq --raw-output '.adb_enabled // true' $CONFIG_PATH)
    ADB_HOST=$(jq --raw-output '.adb_host // "192.168.66.124"' $CONFIG_PATH)
    ADB_PORT=$(jq --raw-output '.adb_port // 5555' $CONFIG_PATH)
    GATEWAY_URL=$(jq --raw-output '.gateway_url // "http://192.168.66.124:8080"' $CONFIG_PATH)
    GATEWAY_TOKEN=$(jq --raw-output '.gateway_token // "x50test"' $CONFIG_PATH)
else
    ADB_ENABLED="true"
    ADB_HOST="192.168.66.124"
    ADB_PORT="5555"
    GATEWAY_URL="http://192.168.66.124:8080"
    GATEWAY_TOKEN="x50test"
fi

echo "[X50 Add-on] Starting X50 Navigation Simulator..."

export X50_GATEWAY_URL="$GATEWAY_URL"
export X50_TOKEN="$GATEWAY_TOKEN"

if [ "$ADB_ENABLED" = "true" ] && [ -n "$ADB_HOST" ]; then
    echo "[X50 Add-on] Connecting ADB in background to ${ADB_HOST}:${ADB_PORT}..."
    (sleep 1 && adb connect "${ADB_HOST}:${ADB_PORT}") >/dev/null 2>&1 &
fi

echo "[X50 Add-on] Launching web controller on port 8090..."
exec python3 /app/server.py --port 8090
