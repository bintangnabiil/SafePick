#!/usr/bin/env bash
# ==============================================================================
# SafePick - Stop Web Server (Raspberry Pi)
# ==============================================================================
set -euo pipefail

PORT="${SAFEPICK_WEB_PORT:-${FACEGATE_WEB_PORT:-8000}}"

echo "[*] Mencari proses di port $PORT..."

# 1. Coba stop systemd service dulu
if systemctl is-active --quiet safepick-web 2>/dev/null; then
    echo "    Menghentikan systemd service safepick-web..."
    sudo systemctl stop safepick-web
    echo "[OK] Service safepick-web dihentikan."
    exit 0
fi

# 2. Cari & kill uvicorn berdasarkan port
PIDS=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' || true)

if [[ -z "$PIDS" ]]; then
    # Fallback: coba lsof
    PIDS=$(lsof -ti :"$PORT" 2>/dev/null || true)
fi

if [[ -z "$PIDS" ]]; then
    echo "[OK] Tidak ada proses yang listen di port $PORT."
    exit 0
fi

for PID in $PIDS; do
    PROC_NAME=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
    echo "    Mematikan PID $PID ($PROC_NAME)..."
    kill "$PID" 2>/dev/null || sudo kill "$PID" 2>/dev/null || true
done

sleep 1

# Verifikasi
STILL=$(ss -tlnp 2>/dev/null | grep ":$PORT " || true)
if [[ -z "$STILL" ]]; then
    echo "[OK] Port $PORT sudah bebas."
else
    echo "[X] Port $PORT masih aktif. Coba: sudo kill -9 $PIDS"
    exit 1
fi
