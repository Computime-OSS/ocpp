#!/usr/bin/env bash
# Create the shared dev venv at the git repo root and install HA + OCPP integration + test tools.
#
# Same venv as run_ha.sh: ${REPO_ROOT}/.ha-venv
#
# Usage (from anywhere):
#   bash ocpp/scripts/setup_venv.sh
# Or from repo root:
#   ./ocpp/scripts/setup_venv.sh
#
# Then:
#   source .ha-venv/bin/activate   # repo root
#   cd ocpp && pytest tests/ -q --no-cov
#   ./ocpp/scripts/run_ha.sh       # repo root

set -e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV_DIR="${REPO_ROOT}/.ha-venv"
REQ="${REPO_ROOT}/ocpp/requirements.txt"

cd "$REPO_ROOT"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating venv at ${VENV_DIR}..."
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip
fi

echo "Installing Home Assistant..."
"$VENV_DIR/bin/pip" install --upgrade homeassistant

echo "Installing OCPP integration runtime deps..."
"$VENV_DIR/bin/pip" install "ocpp>=2.1.0" "websockets>=14.1"

if [[ -f "$REQ" ]]; then
    echo "Installing dev/test deps from ocpp/requirements.txt..."
    "$VENV_DIR/bin/pip" install -r "$REQ"
else
    echo "Warning: $REQ not found, skipping dev deps."
fi

echo ""
echo "Done. Activate and run tests:"
echo "  cd ${REPO_ROOT}"
echo "  source .ha-venv/bin/activate"
echo "  cd ocpp && pytest tests/ -q --no-cov"
echo ""
echo "Run Home Assistant:"
echo "  ./ocpp/scripts/run_ha.sh"
