#!/usr/bin/env bash

CONFIG_PATH="/data/options.json"

if [ -f "$CONFIG_PATH" ]; then
    ADB_ENABLED=$(jq --raw-output '.adb_enabled // true' $CONFIG_PATH)
    ADB_HOST=$(jq --raw-output '.adb_host // "192.168.66.124"' $CONFIG_PATH)
    ADB_PORT=$(jq --raw-output '.adb_port // 5037' $CONFIG_PATH)
    ADB_SERVER_MODE=$(jq --raw-output '.adb_server_mode // true' $CONFIG_PATH)
    ADB_DEVICE=$(jq --raw-output '.adb_device // "emulator-5554"' $CONFIG_PATH)
    GATEWAY_URL=$(jq --raw-output '.gateway_url // "http://192.168.66.124:8080"' $CONFIG_PATH)
    GATEWAY_TOKEN=$(jq --raw-output '.gateway_token // "x50test"' $CONFIG_PATH)
    GEO_BRIDGE_URL=$(jq --raw-output '.geo_bridge_url // empty' $CONFIG_PATH)
    GEO_BRIDGE_TOKEN=$(jq --raw-output '.geo_bridge_token // .gateway_token // "x50test"' $CONFIG_PATH)
else
    ADB_ENABLED="true"
    ADB_HOST="192.168.66.124"
    ADB_PORT="5037"
    ADB_SERVER_MODE="true"
    ADB_DEVICE="emulator-5554"
    GATEWAY_URL="http://192.168.66.124:8080"
    GATEWAY_TOKEN="x50test"
    GEO_BRIDGE_URL=""
    GEO_BRIDGE_TOKEN="x50test"
fi

echo "[X50 Add-on] Starting X50 Navigation Simulator..."

export X50_GATEWAY_URL="$GATEWAY_URL"
export X50_TOKEN="$GATEWAY_TOKEN"

if [ "$ADB_ENABLED" = "true" ] && [ -n "$ADB_HOST" ]; then
    if [ "$ADB_SERVER_MODE" = "true" ]; then
        # Use the ADB server running on the Windows AVD host. This preserves
        # the emulator transport type, so `adb emu geo fix` remains available.
        export ADB_SERVER_SOCKET="tcp:${ADB_HOST}:${ADB_PORT}"
        export X50_DEVICE="$ADB_DEVICE"
        if [ -z "$GEO_BRIDGE_URL" ]; then
            GEO_BRIDGE_URL="http://${ADB_HOST}:18081"
        fi
        export X50_GEO_TRANSPORT="http"
        export X50_GEO_BRIDGE_URL="$GEO_BRIDGE_URL"
        export X50_GEO_BRIDGE_TOKEN="$GEO_BRIDGE_TOKEN"
        echo "[X50 Add-on] Using remote ADB server ${ADB_HOST}:${ADB_PORT}, device ${ADB_DEVICE}."
        (sleep 1 && adb devices) >/dev/null 2>&1 &
    else
        export X50_DEVICE="${ADB_HOST}:${ADB_PORT}"
        export X50_GEO_TRANSPORT="adb"
        echo "[X50 Add-on] Connecting ADB in background to ${X50_DEVICE}..."
        (sleep 1 && adb connect "$X50_DEVICE") >/dev/null 2>&1 &
    fi
fi

echo "[X50 Add-on] Launching web controller on port 8090..."
exec python3 /app/server.py --port 8090
