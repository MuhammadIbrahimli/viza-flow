#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10+ is required."
  exit 1
fi

if [ ! -x "venv/bin/python" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install -q -r requirements.txt
mkdir -p temp

echo "Starting API on http://127.0.0.1:8000 ..."
python run_server.py &
API_PID=$!

cleanup() {
  kill "${API_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 3

if ! command -v cloudflared >/dev/null 2>&1; then
  echo
  echo "cloudflared is not in PATH."
  echo "Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  echo "API is already running at http://127.0.0.1:8000  (GET /ping)"
  wait "${API_PID}"
  exit 1
fi

echo "Starting Cloudflare quick tunnel..."
cloudflared tunnel --url http://127.0.0.1:8000
