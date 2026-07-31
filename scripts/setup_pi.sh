#!/usr/bin/env bash
# ==============================================================================
# SafePick — Raspberry Pi Setup Script (v2)
# ==============================================================================
# Satu command untuk menyiapkan SafePick di Raspberry Pi OS dari nol.
#
# Cara pakai:
#   chmod +x scripts/setup_pi.sh
#   sudo ./scripts/setup_pi.sh
#
# Versi 2 (May 2026): kompatibel dengan Pi OS Trixie + Python 3.13. Memperbaiki
# 7 issue yang teridentifikasi pada deployment fresh setup:
#   - Auto-fix CRLF line endings (kalau clone dari Windows)
#   - Python 3.13 jadi warning (sebelumnya fatal block)
#   - libatlas-base-dev dihapus (sudah diganti libopenblas-dev di Trixie)
#   - CREATE INDEX IF NOT EXISTS diganti dengan procedural check
#   - Auto-upgrade ml_dtypes setelah pip install (fix onnx import di 3.13)
#   - NOPASSWD sudoers untuk systemctl (supaya backend bisa self-restart)
#   - Systemd service dapat User & Group eksplisit
#
# Yang dilakukan:
#   1. Update system + install dependensi sistem (MariaDB, libzbar, dll.)
#   2. Aktifkan kamera + group user
#   3. Setup virtual environment Python
#   4. Install Python dependencies + fix ml_dtypes
#   5. Download & ekstrak model InsightFace buffalo_sc
#   6. Setup database MariaDB (user, database, tabel)
#   7. Buat data directories + .env file
#   8. (Opsional) Install autostart systemd service + NOPASSWD sudoers
# ==============================================================================

# Self-heal: kalau script di-clone dari Windows dengan CRLF line-endings, bash
# error pada line pertama. Fix dengan strip \r di-place sebelum lanjut.
if grep -q $'\r' "$0" 2>/dev/null; then
    sed -i 's/\r$//' "$0"
    echo "[i] CRLF line endings detected & fixed. Re-running script…"
    exec bash "$0" "$@"
fi

set -euo pipefail

# ── Warna ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── Konfigurasi (bisa di-override via environment) ───────────────────────────
DB_NAME="${SAFEPICK_DB_NAME:-${FACEGATE_DB_NAME:-safepick}}"
DB_USER="${SAFEPICK_DB_USER:-${FACEGATE_DB_USER:-safepick}}"
DB_PASS="${SAFEPICK_DB_PASSWORD:-${FACEGATE_DB_PASSWORD:-safepick123}}"
DB_HOST="${SAFEPICK_DB_HOST:-${FACEGATE_DB_HOST:-127.0.0.1}}"
DB_PORT="${SAFEPICK_DB_PORT:-${FACEGATE_DB_PORT:-3306}}"
WEB_PORT="${SAFEPICK_WEB_PORT:-${FACEGATE_WEB_PORT:-8000}}"
VENV_DIR=".venv"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Cek root ─────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}[X] Script ini harus dijalankan sebagai root (sudo).${NC}"
    echo "    sudo $0"
    exit 1
fi

# Dapatkan user asli (bukan root) untuk venv & file ownership
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~$REAL_USER")
if [[ "$REAL_USER" == "root" ]]; then
    echo -e "${RED}[X] Jangan jalankan langsung dari root. Gunakan sudo dari user biasa.${NC}"
    exit 1
fi

echo -e "${GREEN}=======================================================${NC}"
echo -e "${GREEN} SafePick — Raspberry Pi Setup (v2)${NC}"
echo -e "${GREEN}=======================================================${NC}"
echo ""
echo -e "Project root : ${CYAN}$PROJECT_ROOT${NC}"
echo -e "User         : ${CYAN}$REAL_USER${NC}"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: System Update & Dependensi
# ═══════════════════════════════════════════════════════════════════════════════
step_system() {
    echo -e "${CYAN}[1/7] Update system & install dependensi sistem...${NC}"

    echo "    Running apt update..."
    apt-get update

    OS_CODENAME=$(grep -oP 'VERSION_CODENAME=\K\w+' /etc/os-release 2>/dev/null || echo "unknown")
    echo "    Detected OS: $OS_CODENAME"

    # Paket dasar. Catatan:
    # - libatlas-base-dev sudah deprecated di Trixie (diganti libopenblas-dev).
    # - libgtk-3-0 dipakai oleh opencv-python untuk display window (jarang).
    # - liblapack-dev ditambah supaya numpy/scipy build optimal.
    # - dos2unix berjaga-jaga kalau ada file shell lain ber-CRLF.
    PACKAGES=(
        python3 python3-dev python3-venv python3-pip
        mariadb-server mariadb-client libmariadb-dev
        libglib2.0-0 libsm6 libxext6 libxrender-dev
        libgstreamer1.0-0 libgstreamer-plugins-base1.0-0
        libgtk-3-0
        libzbar0
        libopenblas-dev liblapack-dev
        libjpeg-dev libpng-dev libtiff-dev
        ffmpeg
        network-manager
        curl wget unzip git dos2unix
        avahi-daemon
        libgl1
        swig liblgpio-dev
    )

    echo -e "    Menginstall paket..."
    FAILED=()
    for pkg in "${PACKAGES[@]}"; do
        if apt-get install -y -qq "$pkg" 2>/dev/null; then
            :
        else
            FAILED+=("$pkg")
        fi
    done

    if [[ ${#FAILED[@]} -gt 0 ]]; then
        echo -e "    ${YELLOW}[!] ${#FAILED[@]} paket tidak terpasang (biasanya tidak kritis):${NC}"
        for p in "${FAILED[@]}"; do echo "        - $p"; done
    fi

    echo -e "${GREEN}    OK.${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: Aktifkan Kamera
# ═══════════════════════════════════════════════════════════════════════════════
step_camera() {
    echo -e "${CYAN}[2/7] Konfigurasi kamera...${NC}"

    if ls /dev/video* 1>/dev/null 2>&1; then
        echo -e "    Kamera terdeteksi: $(ls /dev/video* 2>/dev/null | tr '\n' ' ')"
    else
        echo -e "${YELLOW}    [!] /dev/video* tidak ditemukan.${NC}"
        echo -e "${YELLOW}        Pastikan kamera USB terpasang atau CSI ribbon tersambung.${NC}"
    fi

    if getent group video >/dev/null; then
        usermod -a -G video "$REAL_USER" 2>/dev/null || true
        echo -e "    User '$REAL_USER' ditambahkan ke group 'video'."
    fi
    if getent group netdev >/dev/null; then
        usermod -a -G netdev "$REAL_USER" 2>/dev/null || true
        echo -e "    User '$REAL_USER' ditambahkan ke group 'netdev' (untuk nmcli WiFi)."
    fi

    # Service SafePick berjalan tanpa desktop login aktif. Polkit bawaan hanya
    # mengizinkan kontrol NetworkManager dari session aktif, sehingga nmcli
    # dari backend ditolak dengan "not authorized to control networking".
    install -d -m 0755 /etc/polkit-1/rules.d
    cat > /etc/polkit-1/rules.d/50-safepick-nm.rules <<POLKIT
polkit.addRule(function(action, subject) {
    if (subject.user == "$REAL_USER" &&
        action.id.indexOf("org.freedesktop.NetworkManager.") == 0) {
        return polkit.Result.YES;
    }
});
POLKIT
    chmod 0644 /etc/polkit-1/rules.d/50-safepick-nm.rules
    echo -e "    Polkit NetworkManager rule dibuat untuk user '$REAL_USER'."

    echo -e "${GREEN}    OK.${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: Virtual Environment
# ═══════════════════════════════════════════════════════════════════════════════
step_venv() {
    echo -e "${CYAN}[3/7] Setup virtual environment Python...${NC}"

    cd "$PROJECT_ROOT"

    PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo "$PYTHON_VER" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VER" | cut -d. -f2)
    echo -e "    Python version: $PYTHON_VER"

    # Python 3.13 dulu sempat block ONNX/InsightFace, tapi setelah upgrade
    # ml_dtypes manual sudah work. Jadi cuma warn, tidak fatal.
    if [[ $PYTHON_MAJOR -ge 3 && $PYTHON_MINOR -ge 13 ]]; then
        echo -e "    ${YELLOW}[!] Python 3.13+ terdeteksi.${NC}"
        echo -e "    ${YELLOW}    Untuk versi ini, ml_dtypes akan di-upgrade otomatis${NC}"
        echo -e "    ${YELLOW}    di step [4/7] supaya InsightFace bisa import.${NC}"
    fi

    if [[ -d "$VENV_DIR" ]]; then
        echo -e "    Virtual environment sudah ada — dipakai ulang (tidak di-recreate)."
    else
        python3 -m venv "$VENV_DIR"
        echo -e "    Virtual environment dibuat di $VENV_DIR"
    fi

    "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel -q
    echo -e "${GREEN}    OK.${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: Install Python Dependencies
# ═══════════════════════════════════════════════════════════════════════════════
step_pip() {
    echo -e "${CYAN}[4/7] Install Python dependencies...${NC}"
    echo -e "    (Ini bisa memakan waktu 5-15 menit di Raspberry Pi — sabar ya)"

    cd "$PROJECT_ROOT"

    "$VENV_DIR/bin/pip" install --timeout 120 --retries 5 -r requirements.txt

    # Python 3.13 fix: onnx 1.19+ butuh ml_dtypes 0.5+ yang punya `float4_e2m1fn`.
    # ml_dtypes versi lama (0.4.x) tidak punya attribute itu sehingga
    # `import insightface` crash dengan AttributeError. Upgrade dulu, baru
    # kunci numpy < 2 supaya opencv-python tetap kompatibel.
    PYTHON_MINOR=$("$VENV_DIR/bin/python" -c 'import sys; print(sys.version_info.minor)')
    if [[ $PYTHON_MINOR -ge 13 ]]; then
        echo -e "    Python 3.13 detected — upgrading ml_dtypes & pinning numpy<2…"
        "$VENV_DIR/bin/pip" install --upgrade ml_dtypes -q 2>/dev/null || true
        "$VENV_DIR/bin/pip" install "numpy>=1.26.4,<2.0.0" -q 2>/dev/null || true
    fi

    # Verifikasi import kritis (jangan fatal — lanjut aja)
    echo -n "    Verifikasi OpenCV... "
    "$VENV_DIR/bin/python" -c "import cv2; print(cv2.__version__)" 2>/dev/null || \
        echo -e "${YELLOW}GAGAL${NC}"

    echo -n "    Verifikasi InsightFace... "
    "$VENV_DIR/bin/python" -c "import insightface; from insightface.app import FaceAnalysis; print(insightface.__version__)" 2>/dev/null || \
        echo -e "${YELLOW}GAGAL — cek dengan: source $VENV_DIR/bin/activate; python -c 'import insightface'${NC}"

    echo -n "    Verifikasi pyzbar... "
    "$VENV_DIR/bin/python" -c "from pyzbar.pyzbar import decode; print('OK')" 2>/dev/null || \
        echo -e "${YELLOW}WARN${NC}"

    echo -n "    Verifikasi mysql-connector... "
    "$VENV_DIR/bin/python" -c "import mysql.connector; print('OK')" 2>/dev/null || \
        echo -e "${YELLOW}WARN${NC}"

    echo -e "${GREEN}    OK.${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5: Download Model InsightFace
# ═══════════════════════════════════════════════════════════════════════════════
step_models() {
    echo -e "${CYAN}[5/7] Download model InsightFace buffalo_sc...${NC}"

    MODELS_DIR="$PROJECT_ROOT/models"
    mkdir -p "$MODELS_DIR"

    if [[ -f "$MODELS_DIR/buffalo_sc/det_500m.onnx" ]] && [[ -f "$MODELS_DIR/buffalo_sc/w600k_mbf.onnx" ]]; then
        echo -e "    Model buffalo_sc sudah ada, melewati download."
    else
        echo -e "    Mengunduh buffalo_sc.zip (~15 MB)..."
        MODEL_ZIP="$MODELS_DIR/buffalo_sc.zip"
        MODEL_URL="https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip"

        wget -q --show-progress -O "$MODEL_ZIP" "$MODEL_URL" || \
            curl -L -o "$MODEL_ZIP" "$MODEL_URL"

        echo -e "    Mengekstrak..."
        unzip -o -q "$MODEL_ZIP" -d "$MODELS_DIR"
        rm "$MODEL_ZIP"

        echo -e "    Model diekstrak ke: $MODELS_DIR/buffalo_sc/"
    fi

    echo -e "${GREEN}    OK.${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6: Setup MariaDB
# ═══════════════════════════════════════════════════════════════════════════════
step_mariadb() {
    echo -e "${CYAN}[6/7] Setup database MariaDB...${NC}"

    systemctl start mariadb 2>/dev/null || service mysql start 2>/dev/null
    systemctl enable mariadb 2>/dev/null || true

    echo -e "    Membuat database, user, & tabel..."

    # Pakai sudo mysql (akses root via unix_socket di Pi default). Heredoc
    # tidak boleh ada single-quote di dalam string SQL (akan termakan shell)
    # — gunakan double-quote untuk identifier non-keyword.
    mysql -u root <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\`
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS '$DB_USER'@'$DB_HOST' IDENTIFIED BY '$DB_PASS';
ALTER USER '$DB_USER'@'$DB_HOST' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'$DB_HOST';
FLUSH PRIVILEGES;

USE \`$DB_NAME\`;

CREATE TABLE IF NOT EXISTS students (
    nis         VARCHAR(32)   NOT NULL PRIMARY KEY,
    nama        VARCHAR(255)  NOT NULL,
    kelas       VARCHAR(64)   NOT NULL,
    created_at  TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS parents (
    id               INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    nis              VARCHAR(32)   NOT NULL,
    nama_ortu        VARCHAR(255)  NOT NULL,
    embedding_index  INT           NOT NULL UNIQUE,
    enrolled_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (nis) REFERENCES students(nis)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS account (
    username       VARCHAR(255) NOT NULL PRIMARY KEY,
    password_hash  VARCHAR(255) NULL
) ENGINE=InnoDB;
SQL

    local admin_hash
    admin_hash=$("$VENV_DIR/bin/python" - <<'PY'
import bcrypt
print(bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode("utf-8"))
PY
)
    mysql -u root "$DB_NAME" -e "INSERT INTO account (username, password_hash) VALUES ('admin', '$admin_hash') ON DUPLICATE KEY UPDATE password_hash = COALESCE(password_hash, VALUES(password_hash));"

    # CREATE INDEX IF NOT EXISTS tidak didukung MariaDB. Cek manual: kalau
    # nama index belum ada di information_schema.statistics, baru CREATE.
    create_index_if_missing() {
        local idx="$1"
        local tbl="$2"
        local cols="$3"
        local exists
        exists=$(mysql -u root -N -s -e "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='$DB_NAME' AND table_name='$tbl' AND index_name='$idx';")
        if [[ "$exists" == "0" ]]; then
            mysql -u root "$DB_NAME" -e "CREATE INDEX $idx ON $tbl($cols);"
        fi
    }
    create_index_if_missing "idx_parents_nis" "parents" "nis"
    create_index_if_missing "idx_parents_embedding" "parents" "embedding_index"

    echo -e "    Database '$DB_NAME' & user '$DB_USER' siap."
    echo -e "    Akun admin default: username=admin, password=admin123"
    echo -e "    ${YELLOW}    GANTI password setelah login pertama via /admin.${NC}"
    echo -e "${GREEN}    OK.${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7: Data Directories & Environment File
# ═══════════════════════════════════════════════════════════════════════════════
step_data() {
    echo -e "${CYAN}[7/7] Setup direktori data & konfigurasi...${NC}"

    cd "$PROJECT_ROOT"

    mkdir -p data/face_db/snapshots
    mkdir -p data/qr_codes
    mkdir -p data/exports
    mkdir -p data/attendance_photos
    mkdir -p data/unknown_photos
    mkdir -p data/tts_cache
    mkdir -p logs

    if [[ ! -f data/settings.json ]]; then
        cat > data/settings.json <<'JSON'
{
    "cam_index": 0,
    "width": 640,
    "height": 480,
    "det_size": 160,
    "min_det_score": 0.6,
    "samples": 10,
    "threshold": 0.35,
    "show_performance": true,
    "mirror_camera": false
}
JSON
        echo -e "    data/settings.json dibuat dengan nilai default."
    else
        echo -e "    data/settings.json sudah ada, melewati..."
    fi

    cat > .env <<ENV
# SafePick — Environment Configuration
# Generated by setup_pi.sh

# Database
SAFEPICK_DB_HOST=$DB_HOST
SAFEPICK_DB_PORT=$DB_PORT
SAFEPICK_DB_USER=$DB_USER
SAFEPICK_DB_PASSWORD=$DB_PASS
SAFEPICK_DB_NAME=$DB_NAME

# Web Server
SAFEPICK_WEB_PORT=$WEB_PORT
ENV

    chown -R "$REAL_USER:$REAL_USER" "$PROJECT_ROOT"

    echo -e "    File .env dibuat."
    echo -e "${GREEN}    OK.${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Autostart Service (Opsional)
# ═══════════════════════════════════════════════════════════════════════════════
step_autostart() {
    echo ""
    echo -n -e "${YELLOW}    Install autostart systemd service? (y/N): ${NC}"
    read -r REPLY
    if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
        return 0
    fi

    echo -e "${CYAN}    Membuat systemd service…${NC}"

    cat > /etc/systemd/system/safepick-web.service <<SVCFILE
[Unit]
Description=SafePick Web Server (FastAPI/Uvicorn)
After=network.target mariadb.service
Wants=mariadb.service

[Service]
Type=simple
User=$REAL_USER
Group=$REAL_USER
WorkingDirectory=$PROJECT_ROOT
EnvironmentFile=$PROJECT_ROOT/.env
ExecStart=$PROJECT_ROOT/$VENV_DIR/bin/uvicorn backend.app:app --host 0.0.0.0 --port \${SAFEPICK_WEB_PORT:-8000}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCFILE

    # NOPASSWD sudoers: supaya backend (jalan sebagai user $REAL_USER) bisa
    # restart/reload service-nya sendiri dari endpoint admin tanpa mengetik
    # password tiap kali. Granular: hanya 3 perintah systemctl ini yang
    # boleh dijalankan tanpa password — tidak buka pintu lebih lebar.
    cat > /etc/sudoers.d/020_safepick-systemctl <<SUDOERS
$REAL_USER ALL=(root) NOPASSWD: /bin/systemctl restart safepick-web
$REAL_USER ALL=(root) NOPASSWD: /bin/systemctl status safepick-web
$REAL_USER ALL=(root) NOPASSWD: /bin/systemctl reload safepick-web
SUDOERS
    chmod 0440 /etc/sudoers.d/020_safepick-systemctl

    systemctl daemon-reload
    systemctl enable safepick-web.service
    systemctl start safepick-web.service

    sleep 2
    if systemctl is-active --quiet safepick-web; then
        echo -e "${GREEN}    Service safepick-web terinstall & RUNNING.${NC}"
    else
        echo -e "${RED}    Service safepick-web GAGAL start. Cek log:${NC}"
        echo "    sudo journalctl -u safepick-web -n 30 --no-pager"
    fi

    echo ""
    echo -e "    ${CYAN}Kelola service:${NC}"
    echo "      sudo systemctl status safepick-web"
    echo "      sudo systemctl restart safepick-web"
    echo "      sudo journalctl -u safepick-web -f"
}

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
main() {
    step_system
    step_camera
    step_venv
    step_pip
    step_models
    step_mariadb
    step_data

    chown -R "$REAL_USER:$REAL_USER" "$PROJECT_ROOT"

    echo ""
    echo -e "${GREEN}=======================================================${NC}"
    echo -e "${GREEN} Setup Selesai ✓${NC}"
    echo -e "${GREEN}=======================================================${NC}"
    echo ""

    LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    [[ -z "$LAN_IP" ]] && LAN_IP="<IP-Raspberry-Pi>"

    echo -e "  ${CYAN}Quick start manual:${NC}"
    echo -e "  cd $PROJECT_ROOT && source $VENV_DIR/bin/activate"
    echo -e "  python -m uvicorn backend.app:app --host 0.0.0.0 --port $WEB_PORT"
    echo ""
    echo -e "  ${CYAN}Access Web UI:${NC}"
    echo -e "  Display : http://$LAN_IP:$WEB_PORT/display"
    echo -e "  Admin   : http://$LAN_IP:$WEB_PORT/admin"
    step_autostart

    echo ""
    echo -e "${GREEN}[OK] Raspberry Pi siap menjalankan SafePick.${NC}"
    echo -e "${YELLOW}[!] Reboot direkomendasikan untuk apply group video & netdev.${NC}"
    echo "    sudo reboot"
}

main
