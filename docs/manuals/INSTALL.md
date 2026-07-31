# Panduan Instalasi SafePick

Dokumen ini dipakai untuk menyiapkan SafePick di laptop sekolah atau Raspberry Pi.

## 1. Kebutuhan

Windows:

- Python 3.10 sampai 3.12.
- XAMPP atau MySQL/MariaDB.
- Kamera/webcam.
- Browser.

Raspberry Pi:

- Raspberry Pi OS.
- Kamera USB.
- LCD touchscreen.
- MariaDB.
- Akses terminal.

## 2. Instalasi Windows

1. Nyalakan MySQL melalui XAMPP.
2. Buka PowerShell di folder project.
3. Install dependency:

```powershell
python -m pip install -r requirements.txt
```

4. Buat database dan tabel:

```powershell
python scripts\db_tools\init_database.py
```

5. Jalankan web server:

```powershell
start_web.bat
```

6. Buka halaman:

```text
Display : http://127.0.0.1:8000/display
Admin   : http://127.0.0.1:8000/admin
Login   : http://127.0.0.1:8000/login
```

## 3. Instalasi Raspberry Pi

Jalankan:

```bash
chmod +x scripts/setup_pi.sh
sudo ./scripts/setup_pi.sh
```

Script akan:

- memasang dependency sistem;
- membuat virtual environment;
- memasang dependency Python;
- menyiapkan MariaDB;
- membuat tabel `students`, `parents`, `qr_tokens`, `attendance_logs`, dan `account`;
- membuat akun admin awal;
- membuat service `safepick-web`.

Perintah service:

```bash
sudo systemctl status safepick-web
sudo systemctl restart safepick-web
sudo journalctl -u safepick-web -f
```

## 4. Database

Database default:

```text
safepick
```

Tabel akun admin:

```sql
CREATE TABLE IF NOT EXISTS account (
    username VARCHAR(255) PRIMARY KEY,
    password_hash VARCHAR(255) NULL
);
```

Kolom `password` plaintext tidak digunakan. Password disimpan sebagai bcrypt hash pada `password_hash`.

## 5. Akun Admin

Setup awal membuat akun:

```text
username: admin
password: admin123
```

Untuk mengganti username dan password:

```bash
python -c "from backend.ui.dashboard import AdminAccountStore; AdminAccountStore().rename_account('admin', 'username_baru', 'password_baru')"
```

Untuk mengganti password saja:

```bash
python -c "from backend.ui.dashboard import AdminAccountStore; AdminAccountStore().create_or_update_account('admin', 'password_baru')"
```

## 6. Alur Operasional Awal

1. Input data siswa di halaman Admin > Database.
2. Enroll wajah orang tua/wali di halaman Admin > Enroll.
3. QR Code akan dibuat otomatis saat enrollment berhasil atau saat admin membuka `Show QR`.
4. Gunakan halaman Display untuk scan kehadiran dan penjemputan.
5. Pantau hasil scan di halaman Admin > Log.

## 7. Troubleshooting Singkat

Tidak bisa login:

- Pastikan MySQL aktif.
- Pastikan tabel `account` memiliki kolom `username` dan `password_hash`.
- Restart server setelah perubahan database atau source code.

Kamera tidak terbaca:

- Cek index kamera di Settings.
- Pastikan kamera tidak sedang dipakai aplikasi lain.

QR sulit terbaca:

- Pastikan QR tidak buram.
- Dekatkan QR ke kamera.
- Gunakan pencahayaan cukup.

Halaman tidak bisa dibuka dari laptop lain:

- Pastikan perangkat satu jaringan.
- Gunakan IP perangkat server, contoh `http://192.168.x.x:8000/admin`.
- Pastikan firewall mengizinkan port 8000.
