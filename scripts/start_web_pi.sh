#!/usr/bin/env bash
# ==============================================================================
# SafePick — Start Web Server (Raspberry Pi)
# ==============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PORT="${SAFEPICK_WEB_PORT:-${FACEGATE_WEB_PORT:-8000}}"
VENV_DIR=".venv"
VENV_PYTHON="$PROJECT_ROOT/$VENV_DIR/bin/python"

# ── Cek apakah venv ada ──
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "[X] Virtual environment tidak ditemukan di $PROJECT_ROOT/$VENV_DIR"
    echo "    Jalankan setup dulu: sudo bash scripts/setup_pi.sh"
    exit 1
fi

# ── Cek apakah port sudah dipakai ──
if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
    echo "[!] Port $PORT sudah digunakan. Coba port lain atau jalankan:"
    echo "    bash scripts/stop_web_pi.sh"
    exit 1
fi

# ── Load .env jika ada ──
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# ── Cek MySQL ──
if ! systemctl is-active --quiet mariadb 2>/dev/null && ! systemctl is-active --quiet mysql 2>/dev/null; then
    echo "[!] MariaDB tidak berjalan. Menjalankan..."
    sudo systemctl start mariadb 2>/dev/null || sudo service mysql start 2>/dev/null || true
fi

# ── Dapatkan LAN IP ──
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -z "$LAN_IP" ]] && LAN_IP="<IP-Raspberry-Pi>"

echo "======================================================="
echo " SafePick Web Server"
echo "======================================================="
echo ""
echo " Project : $PROJECT_ROOT"
echo " Port    : $PORT"
echo " Python  : $($VENV_PYTHON --version)"
echo ""
echo " URL di Raspberry Pi:"
echo "   Display : http://127.0.0.1:$PORT/display"
echo "   Admin   : http://127.0.0.1:$PORT/admin"
echo "   API Docs: http://127.0.0.1:$PORT/docs"
echo ""
echo " URL dari laptop/HP satu jaringan:"
echo "   Display : http://$LAN_IP:$PORT/display"
echo "   Admin   : http://$LAN_IP:$PORT/admin"
echo ""
echo " Tekan CTRL+C untuk mematikan server."
echo "======================================================="
echo ""

"$VENV_PYTHON" -m uvicorn backend.app:app --host 0.0.0.0 --port "$PORT"
