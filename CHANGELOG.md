# 📋 CHANGELOG
**Platform Verifikasi Penjemputan Siswa Berbasis Face Recognition (Edge Computing)**

Format yang digunakan didasarkan pada [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), dan proyek ini mematuhi [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] - 2026-05-16 (Restructure: Frontend/Backend Split)

Refactor besar pemisahan struktur folder agar peran frontend (template + asset) dan backend (Python) jelas terpisah. Tidak ada perubahan fitur.

### Diubah (Changed)
- `src/` direname menjadi `backend/` (semua sub-modul ikut: `core/`, `database/`, `utils/`, `ui/`, `hardware/`).
- `web/app.py` dipindah ke `backend/app.py`.
- `web/templates/` dipindah ke `frontend/templates/`.
- `web/static/` direstruktur ke `frontend/static/css/` dan `frontend/static/js/`.
- Semua import internal Python diperbarui dari `from src.xxx` menjadi `from backend.xxx`.
- Entry point uvicorn diubah dari `web.app:app` menjadi `backend.app:app` di:
  - `main.py` (mode `server`)
  - `scripts/start_web_windows.ps1`
  - `scripts/start_web_pi.sh`
  - `scripts/setup_pi.sh` (systemd autostart)
- Path mount FastAPI di `backend/app.py` diubah ke `frontend/static` dan `frontend/templates`.
- Referensi asset di HTML template menjadi `/static/css/...` dan `/static/js/...`.
- Konfigurasi PyInstaller di `build_v2.bat` dan `scripts/build_pi.sh` disesuaikan ke layout baru.

### Catatan
- URL publik (`/admin`, `/display`, `/login`, `/api/...`, `/video/...`, `/static/...`) tidak berubah.
- Database MySQL, file runtime di `data/`, dan model AI tidak terdampak.
- Setelah pull restructure, jangan lupa hapus `__pycache__/` lama atau jalankan `python -m compileall -q backend`.

---

## [Unreleased] - 2026-05-15 (Dashboard Lokal dan LCD Touchscreen)

Catatan ini dipakai sebagai titik recovery pekerjaan UI dashboard lokal dan UI LCD setelah beberapa kali pull dari GitHub serta penyesuaian database lokal XAMPP.

### Ditambahkan (Added)
- Web dashboard FastAPI dengan login admin di `/login`, dashboard di `/admin`, dan display kamera di `/display`.
- UI LCD touchscreen dengan tiga mode utama: Scan QR Kehadiran, Scan QR Penjemputan, dan Scan Muka Penjemputan.
- Sidebar Log dashboard dengan dropdown tabel terpisah untuk QR Kehadiran, QR Penjemputan, dan Muka Penjemputan.
- Kolom `Nama Siswa` pada tabel log dashboard.
- Tabel `attendance_logs` untuk mencatat NIS, kelas, jenis absen, waktu absen, bukti foto, status, dan pembatalan log.
- Fitur bukti foto untuk setiap scan berhasil, disimpan sebagai JPG di `data/attendance_photos/` dan path-nya disimpan di MySQL.
- Struktur bukti foto per tanggal `YYYY-MM-DD`, lalu per `QR Kehadiran`, `QR Penjemputan`, dan `Muka Penjemputan`.
- Cleanup otomatis bukti foto lama dengan default umur simpan 31 hari.
- Skrip migrasi `scripts/migrate_attendance_photos.py` untuk merapikan bukti foto lama dan memperbarui path di MySQL.
- Popup bukti foto di halaman Log tanpa membuka tab baru.
- Soft-cancel log melalui status `DIBATALKAN`, `cancel_reason`, dan `cancelled_at`.
- Tabel `qr_tokens` untuk mapping token QR unik ke NIS.
- Struktur file QR per kelas di `data/qr_codes/<kelas>/<nis>.png`.
- Skrip migrasi `scripts/migrate_qr_codes.py` untuk memindahkan QR lama ke folder kelas.
- Auto-migration akun login dari database lama `admin.account` ke `facegate_edge.account`.

### Diubah (Changed)
- Login dashboard sekarang memakai database utama `facegate_edge`, tabel `account`; database terpisah `admin` tidak lagi dibutuhkan.
- Isi QR terbaru berupa token random 16 karakter hex, bukan NIS dan bukan format lama `hash:NIS`.
- Mode QR attendance/pickup memakai validasi QR sekaligus deteksi wajah dalam frame yang sama.
- Cooldown pencatatan log scan diatur 60 detik per kombinasi jenis absen dan NIS agar kamera tidak memenuhi log ketika objek yang sama terus terdeteksi.
- Tombol Display dashboard diarahkan ke tab yang sama, bukan membuka tab baru.
- Tombol Home pada halaman LCD dan mode scan memakai ikon Home dari Icons8.

### Diperbaiki (Fixed)
- Perpindahan mode QR Kehadiran dan QR Penjemputan tidak lagi macet karena stream QR lama masih aktif.
- Tombol Home LCD diperbesar, dibuat lebih mudah ditekan, dan diposisikan lebih rapi.
- Teks tombol scan LCD dirapikan agar berada di tengah tombol.
- Tombol API dashboard dihapus karena tidak diperlukan untuk user harian.

### Catatan Recovery
- Jika login bermasalah, cek tabel `facegate_edge.account`.
- Jika bukti foto tidak muncul, cek path di `attendance_logs.bukti_foto` dan folder `data/attendance_photos/`.
- Jika QR lama tidak terbaca, generate ulang QR agar memakai token dari tabel `qr_tokens`.
- Jika perubahan UI tidak muncul, refresh browser setelah memastikan server FastAPI sudah restart.

---

## [v0.0.2] - 2026-03-06 (Fase Persiapan Edge)

Versi ini merupakan perombakan besar-besaran (*Major Refactor*) dari purwarupa awal yang dijalankan di PC, dengan tujuan utama untuk menyiapkan aplikasi agar dapat dipasang (*deploy*) secara mulus pada perangkat **Raspberry Pi** atau perangkat *Edge Computing* lainnya. 

Pembaruan utama berpusat pada perapian standar repositori (*Enterprise Standard*) dan optimalisasi model kecerdasan buatan (*AI Backbone*).

### Ditambahkan (Added)
- Struktur folder termodularisasi secara penuh:
  - `src/`: Untuk logika inti (Core, Database, Utils, Hardware, UI).
  - `scripts/`: Untuk kumpulan skrip utilitas pengelolaan (Manajemen DB & Log).
  - `data/`: Untuk isolasi penyimpanan privat seperti MySQL (`facegate_edge`), embeddings (`embeddings.npy`), dan *QR codes*.
  - `docs/`: Untuk menampung semua dokumen teknis dan laporan pendukung.
- Implementasi Model AI **MobileFaceNet** (`buffalo_sc`). Model ringan yang dirancang khusus untuk perangkat IoT dan *Edge Computing*.
- Dukungan *Dynamic Path Resolution* pada `build_face_app()` agar mesin AI dapat selalu menemukan folder *model* hingga tiga tingkat ke atas, menyelesaikan masalah model hilang saat kompilasi *PyInstaller*.

### Diubah (Changed)
- Nama repositori proyek dari "face_recog_insightface v.0.0.2" menjadi **"FaceGate_Edge"** untuk menghindari *hardcode* nama versi pada direktori utama.
- Penggantian tulang punggung (*Backbone*) AI pada modul `main.py` yang semula menggunakan `buffalo_l` (ResNet50) diganti sepenuhnya menjadi `buffalo_sc` (MobileFaceNet) sesuai dengan yang dijabarkan pada Laporan Tugas Akhir Bab 3.
- Parameter konfigurasi `DET_SIZE` diubah dari resolusi 320x320 menjadi **160x160**, menghasilkan peningkatan performa *Inference Time* secara ekstrem hingga **22x lebih cepat** (166 ms menjadi 7.3 ms) pada hasil *benchmark* uji coba di PC.
- Memperbaiki validasi *prompt input* pada menu utama CLI (Command Line Interface) dari `(1-4)` menjadi `(1-5)`.

### Diperbaiki (Fixed)
- *Bug Error* "os is not defined" dan "qr_manager not defined" pada baris pendaftaran (Enroll) yang menyebabkan proses pembuatan gambar file QR Code (`.png`) selalu *error* di tengah jalan setelah wajah difoto.
- *Bug Error* direktori *key* enkripsi QR Code ("No such file or directory: face_db\\.qr_key") pada saat melakukan verifikasi *Scan QR Code*, disesuaikan ulang menjadi parameter `data/qr_codes` dan `data/face_db`.
- *Issue Orphaned Data*: Pembersihan file `embeddings.npy` dan mereset tabel relasional yang tertumpuk (gagal sinkronisasi) di MySQL agar index pengenalan wajah bersih kembali mulai dari 0.


---

## [v0.0.1] - 2025-12-20 (Fase Prototipe PC)

Versi Prototipe Awal (*Proof of Concept*) yang mendemonstrasikan kelayakan berjalannya sistem pendeteksian dan pengenalan wajah untuk orang tua/wali murid yang terhubung dengan database MySQL dan QR Code terenkripsi.

### Ditambahkan (Added)
- Pengembangan logika awal (Core Logic) pendeteksian dan *Face Embeddings* menggunakan model standar InsightFace `buffalo_l` berbasis arsitektur ResNet50 (275 MB).
- Menu Command Line Interface (CLI) untuk manajemen interaktif:
  - Mode *Enroll* (Pendaftaran Wajah hingga 10 foto rata-rata / *averaging*).
  - Mode *Recognize* (Verifikasi waktu-nyata terhadap data di database).
  - Opsi ganti perangkat Kamera secara *on-the-fly*.
  - Mode *Scan* dan Pembuatan QR Code (Fallback Verifikasi).
- Integrasi *Smart Mode* (*Adaptive Multi-Face Detection*) guna menstabilkan *Frame Per Second* (FPS) pada saat mendeteksi lebih dari tiga wajah sekaligus pada satu frame.
- Integrasi pustaka kriptografi (*Fernet Encryption* & *SHA-256 Hashing*) untuk pengamanan data siswa yang disuntikkan (*inject*) ke dalam kotak QR Code.
- Sistem monitoring utilitas CPU dan RAM, serta subsistem rekam aktivitas/Log (Enrollment, Access, System, Error).

### Keterbatasan (Known Issues)
- Masih memiliki *Bottleneck Performance* (13 FPS, 1.3 GB RAM, dan beban eksekusi CPU yang sangat tinggi) karena mengandalkan model klasifikasi berukuran *Large* pada prosesor non-GPU.
- Belum memiliki fitur GUI layar sentuh (*Touchscreen*).
- Belum mendukung antarmuka ke GPIO Raspberry Pi (Buzzer & Relay LED).
- Struktur direktori masih bercampur (tidak menganut pemisahan MVC/Modularisasi).
