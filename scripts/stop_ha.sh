#!/usr/bin/env bash
# Stop Home Assistant and remove retained resources so HA can start cleanly.
#
# Run when port 8123 or 9000 is already in use (e.g. leftover process or devcontainer),
# or when config/.ha_run.lock remains after a crash or container exit.
#
# Usage (from repo root):
#   ./ocpp/scripts/stop_ha_and_clean.sh
#
# Options:
#   --stop-container   Also stop any Docker container that is binding 8123/9000 (e.g. devcontainer).

set -e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONFIG_DIR="${REPO_ROOT}/config"
LOCK_FILE="${CONFIG_DIR}/.ha_run.lock"
STOP_CONTAINER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stop-container) STOP_CONTAINER=1; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

cd "$REPO_ROOT"

echo "=== Stopping local Home Assistant processes ==="
KILLED=""
for pid in $(pgrep -f "hass -c ${REPO_ROOT}/config" 2>/dev/null); do
    echo "  Stopping hass (PID $pid)"
    kill "$pid" 2>/dev/null || true
    KILLED=1
done
for pid in $(pgrep -f "hass -c ${CONFIG_DIR}" 2>/dev/null); do
    echo "  Stopping hass (PID $pid)"
    kill "$pid" 2>/dev/null || true
    KILLED=1
done
# Fallback: any hass using this config dir
if [[ -z "$KILLED" ]]; then
    for pid in $(pgrep -f "\.ha-venv.*hass" 2>/dev/null); do
        echo "  Stopping hass (PID $pid)"
        kill "$pid" 2>/dev/null || true
        KILLED=1
    done
fi
if [[ -z "$KILLED" ]]; then
    echo "  No local hass process found."
else
    sleep 2
    # Force kill if still running
    for pid in $(pgrep -f "hass -c ${REPO_ROOT}/config" 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
    for pid in $(pgrep -f "\.ha-venv.*hass" 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
fi

echo "=== Removing HA run lock ==="
if [[ -f "$LOCK_FILE" ]]; then
    rm -f "$LOCK_FILE"
    echo "  Removed ${LOCK_FILE}"
else
    echo "  No lock file found."
fi

echo "=== Checking port 8123 / 9000 ==="
P8123=$(lsof -ti :8123 2>/dev/null || true)
P9000=$(lsof -ti :9000 2>/dev/null || true)
if [[ -n "$P8123" ]] || [[ -n "$P9000" ]]; then
    echo "  Port 8123: ${P8123:-free}"
    echo "  Port 9000: ${P9000:-free}"
    if command -v docker >/dev/null 2>&1; then
        CONTAINERS=$(docker ps --filter "publish=8123" --format "{{.ID}} {{.Names}}" 2>/dev/null || true)
        if [[ -n "$CONTAINERS" ]]; then
            echo ""
            echo "  A Docker container is using port 8123 (and possibly 9000):"
            echo "  $CONTAINERS"
            echo "  This is often a devcontainer. To free the port:"
            echo "    docker stop <container_id_or_name>"
            echo "  Or run this script with: ./ocpp/scripts/stop_ha_and_clean.sh --stop-container"
        fi
    fi
else
    echo "  Ports 8123 and 9000 are free."
fi

if [[ -n "$STOP_CONTAINER" ]] && command -v docker >/dev/null 2>&1; then
    echo ""
    echo "=== Stopping Docker container(s) using 8123 or 9000 ==="
    for cid in $(docker ps -q --filter "publish=8123" 2>/dev/null); do
        echo "  Stopping container $cid"
        docker stop "$cid" 2>/dev/null || true
    done
    for cid in $(docker ps -q --filter "publish=9000" 2>/dev/null); do
        echo "  Stopping container $cid"
        docker stop "$cid" 2>/dev/null || true
    done
    echo "  Done. You can start HA with: ./ocpp/scripts/run_ha.sh"
fi

echo ""
echo "Cleanup done. Start HA with: ./ocpp/scripts/run_ha.sh"
