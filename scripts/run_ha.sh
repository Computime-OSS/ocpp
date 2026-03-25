#!/usr/bin/env bash
# Run Home Assistant with the repo config, loading the OCPP integration from ./ocpp.
#
# On first run:
#   1. Creates a Python venv at repo root (.ha-venv) and installs Home Assistant.
#   2. Symlinks ocpp/custom_components/ocpp -> config/custom_components/ocpp.
#
# Usage (from repo root):
#   ./ocpp/scripts/run_ha.sh
# Or:
#   bash ocpp/scripts/run_ha.sh
#
# Then open http://localhost:8123 and add the OCPP integration (port 9000).

set -e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONFIG_DIR="${REPO_ROOT}/config"
VENV_DIR="${REPO_ROOT}/.ha-venv"
CUSTOM_COMPONENTS="${CONFIG_DIR}/custom_components"
OCPP_LINK="${CUSTOM_COMPONENTS}/ocpp"
OCPP_SOURCE="${REPO_ROOT}/ocpp/custom_components/ocpp"

cd "$REPO_ROOT"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating venv at ${VENV_DIR} and installing Home Assistant..."
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install homeassistant
    echo "Home Assistant installed."
fi
# OCPP integration dependencies (required for the custom component to load)
"$VENV_DIR/bin/pip" install -q "ocpp>=2.1.0" "websockets>=14.1"
# Keep venv Home Assistant version in sync with config DB schema (e.g. schema 48 vs 50)
"$VENV_DIR/bin/pip" install -q --upgrade homeassistant

mkdir -p "$CUSTOM_COMPONENTS"
if [[ -L "$OCPP_LINK" ]]; then
    # Ensure it points to our source
    current=$(readlink -f "$OCPP_LINK" 2>/dev/null || readlink "$OCPP_LINK")
    if [[ "$current" != "$OCPP_SOURCE" ]]; then
        rm -f "$OCPP_LINK"
        ln -sf "$OCPP_SOURCE" "$OCPP_LINK"
    fi
elif [[ ! -e "$OCPP_LINK" ]]; then
    ln -sf "$OCPP_SOURCE" "$OCPP_LINK"
fi

if [[ ! -d "$CONFIG_DIR" ]]; then
    echo "Error: config directory not found at ${CONFIG_DIR}. Create it (e.g. copy from a HA install) or run HA once to let it create defaults."
    exit 1
fi

# Remove stale lock so we can start (e.g. after crash or container exit)
[[ -f "${CONFIG_DIR}/.ha_run.lock" ]] && rm -f "${CONFIG_DIR}/.ha_run.lock"

if lsof -ti :8123 >/dev/null 2>&1; then
    echo "Error: port 8123 is already in use (often a devcontainer or another HA instance)."
    echo "Run: ./ocpp/scripts/stop_ha_and_clean.sh"
    echo "  or: ./ocpp/scripts/stop_ha_and_clean.sh --stop-container   # if a Docker container holds the port"
    exit 1
fi

echo "Starting Home Assistant (config: ${CONFIG_DIR})..."
echo "  UI: http://localhost:8123"
echo "  OCPP WebSocket: default port 9000"
exec "$VENV_DIR/bin/hass" -c "$CONFIG_DIR"
