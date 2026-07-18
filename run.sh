#!/bin/bash
# CardioSentry demo runner: tunnel first, then server with PUBLIC_BASE_URL baked in.
set -e
cd "$(dirname "$0")"

echo "Starting cloudflared tunnel..."
cloudflared tunnel --url http://localhost:8000 > tunnel.log 2>&1 &
TUNNEL_PID=$!
trap "kill $TUNNEL_PID 2>/dev/null" EXIT

URL=""
for i in $(seq 1 30); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' tunnel.log | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done

if [ -z "$URL" ]; then
  echo "Tunnel failed to start — check tunnel.log"; exit 1
fi

echo ""
echo "================================================="
echo "  PUBLIC URL:   $URL"
echo "  Test page:    $URL/upload-qr   <- open this on the laptop, scan with phone"
echo "  Demo index:   $URL/"
echo "================================================="
echo ""

export PUBLIC_BASE_URL="$URL"
exec ./.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
