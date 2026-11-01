#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is not installed. Install Docker first."
  exit 1
fi

if [ ! -f .env ]; then
  echo "ERROR: .env is missing. Create it from .env.example before deploying."
  exit 1
fi

chmod 600 .env
mkdir -p data

echo "Building PBF Temperature Dashboard v6.0.3 Cloud Edition..."
docker compose build --pull

echo "Starting dashboard on VPS loopback only (127.0.0.1:8000)..."
docker compose up -d

echo
sleep 3
docker compose ps

echo
printf '%s\n' "Secure test mode is running." \
  "Use an SSH tunnel from Windows:" \
  "  ssh -L 8000:127.0.0.1:8000 ubuntu@YOUR_VPS_IP" \
  "Then browse: http://localhost:8000" \
  "Automatic Gmail/ntfy alarms remain controlled by NOTIFICATIONS_ENABLED in .env."
