# SafePick

SafePick adalah perangkat lunak verifikasi penjemputan siswa berbasis pengenalan wajah dan QR Code. Sistem berjalan lokal di perangkat sekolah, memakai FastAPI, MySQL/MariaDB, kamera, dan model InsightFace `buffalo_sc`.

## Fitur Utama

- UI Display di `/display` untuk layar LCD atau perangkat gate.
- UI Admin di `/admin` untuk laptop sekolah.
- Login admin dengan tabel `account`.
- Database siswa, orang tua/wali, QR token, dan log aktivitas.
- Enrollment wajah orang tua/wali.
- Mode scan:
  - QR Kehadiran Siswa
  - QR Penjemputan Non-orang Tua
  - Muka Penjemputan Orang Tua
  - Muka Tidak Dikenal
- Bukti foto tersimpan lokal dan path-nya dicatat di database.
- QR Code berisi token unik, bukan NIS langsung.
- Status log ditampilkan sebagai `VALID` atau `TIDAK VALID`.

## Struktur Folder Final

```text
backend/                 Backend FastAPI, database, face engine, QR, hardware helper
frontend/                Template HTML, CSS, JavaScript, gambar UI
models/                  Model InsightFace buffalo_sc
scripts/                 Script setup, start, stop, dan inisialisasi database
data/face_db/README.md   Penanda folder data face database runtime
docs/manuals/INSTALL.md  Panduan instalasi ringkas untuk mitra
requirements.txt         Dependency Python
start_web.bat            Start web server di Windows
stop_web.bat             Stop web server di Windows
setup_windows.bat        Setup dependency Windows
main.py                  CLI pendukung untuk mode lokal
```

## Kebutuhan

Windows/laptop sekolah:

- Python 3.10 sampai 3.12 direkomendasikan.
- XAMPP atau MySQL/MariaDB aktif.
- Kamera/webcam.
- Browser modern.

Raspberry Pi:

- Raspberry Pi OS.
- Kamera USB.
- LCD touchscreen.
- MariaDB.
- Python virtual environment.

## Setup Windows

1. Nyalakan MySQL dari XAMPP.
2. Buka terminal di folder project.
3. Jalankan setup Windows:

```powershell
setup_windows.bat
```

Atau install dependency dan inisialisasi database secara manual:

```powershell
python -m pip install -r requirements.txt
python scripts\db_tools\init_database.py
```

4. Jalankan server:

```powershell
start_web.bat
```

Atau:

```powershell
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

5. Buka:

```text
Display : http://127.0.0.1:8000/display
Admin   : http://127.0.0.1:8000/admin
Login   : http://127.0.0.1:8000/login
```

## Setup Raspberry Pi

Jalankan:

```bash
chmod +x scripts/setup_pi.sh
sudo ./scripts/setup_pi.sh
```

Script ini menyiapkan dependency sistem, Python virtual environment, MariaDB, tabel database, akun admin awal, folder data runtime, dan service `safepick-web`.

Perintah service:

```bash
sudo systemctl status safepick-web
sudo systemctl restart safepick-web
sudo journalctl -u safepick-web -f
```

## Database

Database default:

```text
safepick
```

Tabel utama:

```text
students
parents
qr_tokens
attendance_logs
account
```

Schema akun admin final:

```sql
CREATE TABLE IF NOT EXISTS account (
    username VARCHAR(255) PRIMARY KEY,
    password_hash VARCHAR(255) NULL
);
```

Kolom `password` plaintext tidak digunakan lagi. Password admin disimpan dalam bentuk bcrypt hash pada kolom `password_hash`.

## Data Runtime

Folder berikut akan dibuat otomatis saat sistem digunakan:

```text
data/attendance_photos/
data/unknown_photos/
data/qr_codes/
data/tts_cache/
data/face_db/
logs/
```

Folder runtime tersebut tidak perlu dikirim berisi data lama ke mitra. Data siswa, orang tua, enrollment, QR, dan log sebaiknya dibuat ulang di lokasi implementasi.

## Model

Pastikan folder model tersedia:

```text
models/buffalo_sc/
```

Minimal file penting:

```text
det_500m.onnx
w600k_mbf.onnx
```

## Akun Admin

Setup awal membuat akun:

```text
username: admin
password: admin123
```

Ganti username dan password sebelum sistem dipakai permanen:

```powershell
python -c "from backend.ui.dashboard import AdminAccountStore; AdminAccountStore().rename_account('admin', 'username_baru', 'password_baru')"
```

Jika hanya ingin mengganti password untuk username yang sama:

```powershell
python -c "from backend.ui.dashboard import AdminAccountStore; AdminAccountStore().create_or_update_account('admin', 'password_baru')"
```

## Catatan Implementasi

- Jalankan MySQL/MariaDB sebelum membuka halaman Admin.
- Gunakan `/display` di perangkat gate/LCD.
- Gunakan `/admin` dari laptop sekolah pada jaringan yang sama.
- Enrollment wajah dilakukan untuk orang tua/wali, bukan wajah siswa.
- QR Code memakai token unik yang merujuk ke NIS di database.
- Bukti foto tidak disimpan sebagai BLOB di MySQL; database hanya menyimpan path file.
