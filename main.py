#!/usr/bin/env python3
"""
Main entry point untuk SafePick.
Simplified interface untuk enroll dan recognize
"""

import warnings
# Suppress all Deprecation and Future warnings from PyTorch/pynvml
warnings.simplefilter("ignore", category=FutureWarning)

import sys
import os
import cv2
import json

# Fix for Windows CMD Unicode Error
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from backend.core.face_engine import (
    build_face_app,
    FaceDB,
    enroll_mode,
    recognize_mode,
    open_camera,
    ensure_opencv_gui
)
from backend.utils.logger import get_logger

# Try to import QR manager (optional - may fail if DLLs missing)
try:
    from backend.core.qr_manager import QRCodeManager
    QR_AVAILABLE = True
except Exception as e:
    print(f"[!] QR Code feature unavailable: {e}")
    print("    Program will continue without QR code support.")
    QR_AVAILABLE = False
    QRCodeManager = None

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich import box

# Initialize logger
logger = get_logger()
console = Console()


def get_app_root():
    """Resolve app root consistently for script and bundled exe runs."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_ROOT = get_app_root()


def app_path(*parts):
    return os.path.join(APP_ROOT, *parts)


SETTINGS_PATH = app_path("data", "settings.json")

# ASCII Art Logo
SAFEPICK_LOGO = """
 ███████████                           █████████           █████          
░░███░░░░░░█                          ███░░░░░███         ░░███           
 ░███   █ ░   ██████   ██████  █████ ░███    ░░░   ██████  ░███████  █████
 ░███████    ░░░░░███ ███░░██████░░█ ░███         ░░░░░███ ░███░░██████░░█
 ░███░░░█     ███████░███ ░░░░█████  ░███    █████ ███████ ░███ ░██░█████ 
 ░███  ░     ███░░███░███  ███░███░  ░░███  ░░███ ███░░███ ░███ ░██░███░  
 █████      ░░████████░░██████░░██████░░█████████░░████████████████░░██████
░░░░░        ░░░░░░░░  ░░░░░░  ░░░░░░  ░░░░░░░░░  ░░░░░░░░░░░░░░░░  ░░░░░░ 
"""

def generate_gradient(start_hex, end_hex, num_steps):
    """Generate a list of hex colors representing a gradient."""
    start_r = int(start_hex[1:3], 16)
    start_g = int(start_hex[3:5], 16)
    start_b = int(start_hex[5:7], 16)
    
    end_r = int(end_hex[1:3], 16)
    end_g = int(end_hex[3:5], 16)
    end_b = int(end_hex[5:7], 16)
    
    colors = []
    for i in range(num_steps):
        ratio = i / max(1, num_steps - 1)
        r = int(start_r + (end_r - start_r) * ratio)
        g = int(start_g + (end_g - start_g) * ratio)
        b = int(start_b + (end_b - start_b) * ratio)
        colors.append(f"#{r:02x}{g:02x}{b:02x}")
    return colors

def apply_gradient(text_art, start_color="#FF0000", end_color="#FFA500"):
    lines = text_art.strip('\n').split('\n')
    if not lines:
        return Text()
    
    max_len = max(len(line) for line in lines)
    colors = generate_gradient(start_color, end_color, max_len)
    
    colored_text = Text()
    for line in lines:
        for i, char in enumerate(line):
            color_idx = min(i, len(colors) - 1)
            colored_text.append(char, style=f"bold {colors[color_idx]}")
        colored_text.append('\n')
        
    return colored_text

def print_header():
    """Print the stylized header"""
    # Clear screen based on OS
    os.system('cls' if os.name == 'nt' else 'clear')
    
    # Print Logo with Red-to-Orange Gradient
    console.print(apply_gradient(SAFEPICK_LOGO, start_color="#FF0000", end_color="#FFA500"))
    
    # Print System Info Panel
    info_text = Text()
    info_text.append("System: ", style="bold white")
    info_text.append("Face Recognition Edge Computing\n", style="cyan")
    info_text.append("Model: ", style="bold white")
    info_text.append("MobileFaceNet (buffalo_sc)\n", style="cyan")
    info_text.append("Status: ", style="bold white")
    info_text.append("Active", style="bold green")
    
    console.print(Panel(
        info_text, 
        border_style="red", 
        box=box.ROUNDED,
        width=85
    ))
    console.print("\n" + "─" * 85, style="dim")

def get_default_settings():
    return {
        "cam_index": 0,
        "width": 640,
        "height": 480,
        "det_size": 160,
        "min_det_score": 0.6,
        "samples": 10,
        "auto_capture_enroll": True,
        "auto_capture_interval_ms": 1200,
        "voice_announcement_enabled": True,
        "voice_announcement_language": "id",
        "voice_announcement_cooldown_seconds": 60,
        "voice_announcement_template": "Ananda {nama_anak} telah dijemput.",
        "threshold": 0.35,
        "show_performance": True,
        "mirror_camera": False
    }


def load_settings():
    settings = get_default_settings()

    if not os.path.exists(SETTINGS_PATH):
        return settings

    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        settings.update(loaded)
    except Exception as e:
        print(f"[!] Gagal membaca settings, menggunakan default. Detail: {e}")

    return settings


def save_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2)


def print_menu(cam_index):
    print_header()
    
    # Menu Options
    console.print("\n[bold white] > Main Menu[/bold white]\n")
    console.print("  [bold red]1.[/bold red] [white]Enroll[/white] [dim](Daftarkan wajah baru)[/dim]")
    console.print("  [bold red]2.[/bold red] [white]Recognize[/white] [dim](Kenali wajah)[/dim]")
    console.print(f"  [bold red]3.[/bold red] [white]Switch Camera[/white] [dim](Saat ini: Camera {cam_index})[/dim]")
    
    if QR_AVAILABLE:
        console.print("  [bold red]4.[/bold red] [white]QR Code Menu[/white]")
    else:
        console.print("  [bold red]4.[/bold red] [dim]QR Code Menu (UNAVAILABLE - DLL missing)[/dim]")
    
    console.print("  [bold red]5.[/bold red] [white]Settings[/white] [dim](Threshold, resolusi, samples, overlay)[/dim]")
    console.print("  [bold red]6.[/bold red] [white]Exit[/white]\n")
    console.print("▄" * 85, style="red")


def test_camera(cam_index, width=640, height=480, mirror_camera=False):
    """Test kamera untuk memastikan berfungsi"""
    print(f"\n[TEST CAMERA {cam_index}]")
    print("Tekan 'q' untuk keluar dari preview.\n")

    try:
        ensure_opencv_gui()
        cap = open_camera(cam_index, width, height)
        print(f"Camera {cam_index} berhasil dibuka!")
        print("Menampilkan preview...\n")

        while True:
            ok, frame = cap.read()
            if not ok:
                print("Gagal membaca frame dari kamera.")
                break
            if mirror_camera:
                frame = cv2.flip(frame, 1)

            # Tampilkan info di frame
            cv2.putText(
                frame,
                f"Camera {cam_index} - Press 'q' to quit",
                (10,
                 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0,
                 255,
                 0),
                2)

            cv2.imshow(f"Camera {cam_index} Test", frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cap.release()
        cv2.destroyAllWindows()
        return True

    except Exception as e:
        print(f"[X] Error: Camera {cam_index} tidak dapat dibuka.")
        print(f"    Detail: {e}")
        return False


def switch_camera(current_index, width=640, height=480, mirror_camera=False):
    """Menu untuk switch camera"""
    print("\n" + "=" * 50)
    print("  SWITCH CAMERA")
    print("=" * 50)
    print(f"Camera saat ini: {current_index}")
    print("\nPilihan:")
    print("1. Test camera saat ini")
    print("2. Ganti ke camera index lain")
    print("3. Kembali ke menu utama")
    print("=" * 50)

    choice = input("\nPilih (1-3): ").strip()

    if choice == "1":
        # Test camera saat ini
        test_camera(current_index, width, height, mirror_camera)
        return current_index

    elif choice == "2":
        # Ganti camera index
        try:
            new_index = int(
                input("\nMasukkan camera index baru (0, 1, 2, ...): ").strip())

            # Test camera baru
            print(f"\nMencoba membuka camera {new_index}...")
            if test_camera(new_index, width, height, mirror_camera):
                print(f"\n[OK] Camera {new_index} berfungsi dengan baik!")
                confirm = input(
                    f"Gunakan camera {new_index}? (y/n): ").strip().lower()
                if confirm == 'y':
                    print(f"\n[OK] Camera diubah ke index {new_index}")
                    return new_index
                else:
                    print(f"\n[!] Tetap menggunakan camera {current_index}")
                    return current_index
            else:
                print(f"\n[X] Camera {new_index} tidak tersedia.")
                print(f"    Tetap menggunakan camera {current_index}")
                return current_index

        except ValueError:
            print("\n[X] Input tidak valid! Harus berupa angka.")
            return current_index
        except Exception as e:
            print(f"\n[X] Error: {e}")
            return current_index

    else:
        # Kembali ke menu utama
        return current_index


def validate_camera_index(cam_index: int, width: int, height: int) -> int:
    """Ensure the configured camera index is actually available."""
    candidates = [cam_index]
    if cam_index != 0:
        candidates.append(0)

    for candidate in candidates:
        try:
            cap = open_camera(candidate, width, height)
            cap.release()
            if candidate != cam_index:
                print(
                    f"[!] Camera {cam_index} tidak tersedia. Otomatis pindah ke camera {candidate}.")
            return candidate
        except Exception:
            continue

    raise RuntimeError(
        f"Tidak ada camera yang tersedia dari kandidat index: {candidates}")


def print_settings_summary(settings):
    print("\nCurrent Settings:")
    print(f"  Camera Index       : {settings['cam_index']}")
    print(f"  Resolution         : {settings['width']}x{settings['height']}")
    print(f"  Recognize Threshold: {settings['threshold']:.2f}")
    print(f"  Min Det Score      : {settings['min_det_score']:.2f}")
    print(f"  Enroll Samples     : {settings['samples']}")
    print(f"  Det Size           : {settings['det_size']}")
    print(f"  Perf Overlay       : {'ON' if settings['show_performance'] else 'OFF'}")
    print(f"  Mirror Camera      : {'ON' if settings['mirror_camera'] else 'OFF'}")


def view_database_menu():
    """Simple database viewer from settings menu."""
    from backend.database.student_db import StudentDatabase
    import time

    student_db = StudentDatabase()
    export_dir = app_path("data", "exports")

    while True:
        print("\n" + "=" * 55)
        print("  LIHAT DATABASE")
        print("=" * 55)
        print("1. Lihat semua siswa")
        print("2. Lihat parent yang sudah enroll")
        print("3. Ringkasan database")
        print("4. Export siswa ke CSV")
        print("5. Kembali ke settings")
        print("=" * 55)

        choice = input("\nPilih (1-5): ").strip()

        if choice == "1":
            students = student_db.list_all_students()
            print(f"\nTotal siswa: {len(students)}")
            if not students:
                print("[!] Belum ada data siswa.")
            else:
                for student in students:
                    print(f"  {student['nis']} | {student['nama']} | {student['kelas']}")
            input("\nTekan ENTER untuk lanjut...")

        elif choice == "2":
            parents = student_db.list_all_parents()
            print(f"\nTotal parent enrolled: {len(parents)}")
            if not parents:
                print("[!] Belum ada parent yang enroll.")
            else:
                for parent in parents:
                    print(
                        f"  NIS {parent['nis']} | Ortu: {parent['nama_ortu']} | Anak: {parent['nama_anak']} | {parent['kelas']}")
            input("\nTekan ENTER untuk lanjut...")

        elif choice == "3":
            students = student_db.list_all_students()
            parents = student_db.list_all_parents()
            enrolled_nis = len({parent['nis'] for parent in parents})
            print("\nRingkasan:")
            print(f"  Total siswa          : {len(students)}")
            print(f"  Total parent enrolled: {len(parents)}")
            print(f"  Siswa sudah enroll   : {enrolled_nis}")
            print(f"  Siswa belum enroll   : {max(0, len(students) - enrolled_nis)}")
            input("\nTekan ENTER untuk lanjut...")

        elif choice == "4":
            os.makedirs(export_dir, exist_ok=True)
            default_name = f"students_export_{time.strftime('%Y%m%d_%H%M%S')}.csv"
            filename = input(
                f"Nama file CSV [{default_name}]: ").strip() or default_name

            if not filename.lower().endswith(".csv"):
                filename += ".csv"

            csv_path = os.path.join(export_dir, filename)
            count = student_db.export_students_to_csv(csv_path)
            print(f"\n[OK] Export CSV berhasil: {count} siswa")
            print(f"     File: {csv_path}")
            input("\nTekan ENTER untuk lanjut...")

        elif choice == "5":
            return

        else:
            print("[X] Pilihan tidak valid")


def settings_menu(settings):
    """Runtime settings menu with persistent save."""
    while True:
        print("\n" + "=" * 55)
        print("  SETTINGS")
        print("=" * 55)
        print_settings_summary(settings)
        print("\nSaran isi menu settings yang paling berguna:")
        print("  - Threshold 0.35 untuk balance akurasi")
        print("  - Min det score 0.60 untuk filter deteksi lemah")
        print("  - Samples 10 untuk enroll stabil")
        print("  - Resolusi 640x480 untuk CPU")
        print("  - Det size 160 untuk performa ringan")
        print("\nPilihan:")
        print("1. Ubah threshold recognize")
        print("2. Ubah minimum detection score")
        print("3. Ubah jumlah sample enroll")
        print("4. Ubah resolusi kamera")
        print("5. Ubah det_size model")
        print("6. Toggle performance overlay")
        print("7. Toggle mirror camera")
        print("8. Lihat database")
        print("9. Reset ke default")
        print("10. Kembali ke menu utama")
        print("=" * 55)

        choice = input("\nPilih (1-10): ").strip()

        try:
            if choice == "1":
                value = float(input("Threshold baru (0.10 - 0.90): ").strip())
                if not 0.10 <= value <= 0.90:
                    raise ValueError("Threshold harus antara 0.10 - 0.90")
                settings["threshold"] = value
                save_settings(settings)
                print(f"[OK] Threshold diubah ke {value:.2f}")

            elif choice == "2":
                value = float(input("Min detection score baru (0.10 - 0.99): ").strip())
                if not 0.10 <= value <= 0.99:
                    raise ValueError("Min detection score harus antara 0.10 - 0.99")
                settings["min_det_score"] = value
                save_settings(settings)
                print(f"[OK] Min detection score diubah ke {value:.2f}")

            elif choice == "3":
                value = int(input("Jumlah sample enroll baru (1 - 30): ").strip())
                if not 1 <= value <= 30:
                    raise ValueError("Samples harus antara 1 - 30")
                settings["samples"] = value
                save_settings(settings)
                print(f"[OK] Enroll samples diubah ke {value}")

            elif choice == "4":
                print("\nPreset resolusi:")
                print("1. 320x240 (ringan)")
                print("2. 640x480 (recommended)")
                print("3. 1280x720 (lebih detail)")
                preset = input("Pilih (1-3): ").strip()
                if preset == "1":
                    settings["width"], settings["height"] = 320, 240
                elif preset == "2":
                    settings["width"], settings["height"] = 640, 480
                elif preset == "3":
                    settings["width"], settings["height"] = 1280, 720
                else:
                    raise ValueError("Preset resolusi tidak valid")
                save_settings(settings)
                print(f"[OK] Resolusi diubah ke {settings['width']}x{settings['height']}")

            elif choice == "5":
                print("\nPreset det_size:")
                print("1. 160 (recommended untuk CPU)")
                print("2. 320 (lebih akurat)")
                print("3. 640 (lebih berat)")
                preset = input("Pilih (1-3): ").strip()
                if preset == "1":
                    settings["det_size"] = 160
                elif preset == "2":
                    settings["det_size"] = 320
                elif preset == "3":
                    settings["det_size"] = 640
                else:
                    raise ValueError("Preset det_size tidak valid")
                save_settings(settings)
                print(f"[OK] Det size diubah ke {settings['det_size']}")
                return "reload_model"

            elif choice == "6":
                settings["show_performance"] = not settings["show_performance"]
                save_settings(settings)
                print(f"[OK] Performance overlay {'aktif' if settings['show_performance'] else 'nonaktif'}")

            elif choice == "7":
                settings["mirror_camera"] = not settings["mirror_camera"]
                save_settings(settings)
                print(f"[OK] Mirror camera {'aktif' if settings['mirror_camera'] else 'nonaktif'}")

            elif choice == "8":
                view_database_menu()

            elif choice == "9":
                settings.clear()
                settings.update(get_default_settings())
                save_settings(settings)
                print("[OK] Settings direset ke default")
                return "reload_model"

            elif choice == "10":
                return None

            else:
                print("[X] Pilihan tidak valid")

        except ValueError as e:
            print(f"[X] {e}")
        except Exception as e:
            print(f"[X] Error settings: {e}")


def qr_code_menu(cam_index: int, mirror_camera: bool = False):
    """Menu untuk QR Code operations"""
    qr_manager = QRCodeManager(
        db_dir=app_path("data", "face_db"),
        qr_dir=app_path("data", "qr_codes"))

    while True:
        print("\n" + "=" * 50)
        print("  QR CODE MENU")
        print("=" * 50)
        print("1. Generate QR Codes untuk Semua")
        print("2. Scan QR Code")
        print("3. Kembali ke Menu Utama")
        print("=" * 50)

        choice = input("\nPilih (1-3): ").strip()

        if choice == "1":
            # Generate QR codes for all students with enrolled parents
            print("\n[*] Generating QR Codes...")

            from backend.database.student_db import StudentDatabase
            student_db = StudentDatabase()

            parents = student_db.list_all_parents()

            if not parents:
                print("\n[!] Belum ada orang tua yang terdaftar.")
                print("    Silakan enroll wajah terlebih dahulu (Menu 1).")
                input("\nTekan ENTER untuk kembali...")
                continue

            # Get unique NIS (one QR per student, not per parent)
            unique_nis = list(set([p['nis'] for p in parents]))

            count = 0
            for nis in unique_nis:
                qr_path = os.path.join("data/qr_codes", f"{nis}.png")
                if not os.path.exists(qr_path):
                    success = qr_manager.generate_qr_code(nis, silent=False)
                    if success:
                        count += 1
                else:
                    print(f"[!] QR already exists: {nis}.png (skipped)")

            if count > 0:
                print(f"\n[OK] {count} QR codes telah di-generate!")
                print(f"     Lokasi: qr_codes/")
                print(f"     Total students: {len(unique_nis)}")
                print("\n[!] Satu QR code per siswa.")
                print("    QR ini bisa digunakan oleh semua orang tua siswa tersebut.")
            else:
                print(
                    f"\n[!] Semua QR code sudah ada ({len(unique_nis)} students).")

            input("\nTekan ENTER untuk kembali...")

        elif choice == "2":
            # Scan QR code
            print(f"\n[*] Membuka camera {cam_index} untuk scan QR code...")
            result = qr_manager.scan_qr_from_camera(
                cam_index=cam_index,
                mirror_camera=mirror_camera)

            if result:
                print(f"\n[OK] QR Code berhasil di-scan!")

                # Get NIS from QR
                nis = result.get('nis')

                if not result.get('verified'):
                    print(f"     NIS: {nis or '-'}")
                    print(f"\n[X] QR code tidak valid atau hash tidak cocok!")
                    logger.log_access(
                        f"NIS:{nis}" if nis else "Invalid QR",
                        granted=False,
                        reason="QR Code scan - Invalid hash")

                elif nis:
                    # Lookup student from database
                    from backend.database.student_db import StudentDatabase
                    student_db = StudentDatabase()

                    parent = student_db.get_parent_by_nis(nis)

                    if parent:
                        print(f"     NIS: {parent['nis']}")
                        print(f"     Nama Ortu: {parent['nama_ortu']}")
                        print(f"     Nama Anak: {parent['nama_anak']}")
                        print(f"     Kelas: {parent['kelas']}")
                        print(f"\n[OK] Verifikasi berhasil!")
                        logger.log_access(
                            f"NIS:{nis}", granted=True, reason="QR Code scan")
                    else:
                        print(f"     NIS: {nis}")
                        print(f"\n[X] Data tidak ditemukan di database!")
                        logger.log_access(
                            f"NIS:{nis}",
                            granted=False,
                            reason="QR Code scan - Data not found")
                else:
                    print(f"\n[X] QR code tidak valid!")
                    logger.log_access(
                        "Invalid QR",
                        granted=False,
                        reason="QR Code scan - Invalid format")
            else:
                print(f"\n[!] Tidak ada QR code yang ter-scan.")

            input("\nTekan ENTER untuk kembali...")

        elif choice == "3":
            break

        else:
            print("\n[X] Pilihan tidak valid!")


def main():
    settings = load_settings()

    # Konfigurasi default/runtime
    DB_DIR = app_path("data", "face_db")
    # buffalo_sc (MobileFaceNet) sangat ringan, cocok untuk Raspberry Pi
    MODEL_NAME = "buffalo_sc"
    DEVICE = "cpu"  # ganti ke "cuda" jika punya GPU
    CAM_INDEX = settings["cam_index"]
    WIDTH = settings["width"]
    HEIGHT = settings["height"]
    DET_SIZE = settings["det_size"]
    MIN_DET_SCORE = settings["min_det_score"]
    SAMPLES = settings["samples"]
    THRESHOLD = settings["threshold"]
    SHOW_PERFORMANCE = settings["show_performance"]
    MIRROR_CAMERA = settings["mirror_camera"]
    AUTO_CAPTURE_ENROLL = bool(settings.get("auto_capture_enroll", True))
    AUTO_CAPTURE_INTERVAL = float(settings.get("auto_capture_interval_ms", 1200)) / 1000.0

    print("\n[*] Memuat model InsightFace...")
    print(f"   Model: {MODEL_NAME}")
    print(f"   Device: {DEVICE}")
    print(f"   Camera: {CAM_INDEX}")

    try:
        CAM_INDEX = validate_camera_index(CAM_INDEX, WIDTH, HEIGHT)
        settings["cam_index"] = CAM_INDEX
        save_settings(settings)
    except Exception as e:
        logger.log_error("CameraInitError", str(e))
        raise

    logger.log_system(
        f"System started | Model: {MODEL_NAME} | Device: {DEVICE} | Camera: {CAM_INDEX}")

    # Inisialisasi
    db = FaceDB(DB_DIR)

    try:
        app = build_face_app(
            model_name=MODEL_NAME,
            det_size=DET_SIZE,
            device=DEVICE)
        logger.log_model_load(MODEL_NAME, DEVICE, success=True)
        print("[OK] Model berhasil dimuat!\n")
    except Exception as e:
        logger.log_model_load(MODEL_NAME, DEVICE, success=False)
        logger.log_error("ModelLoadError", str(e))
        raise

    while True:
        print_menu(CAM_INDEX)
        choice = input(f"\nPilih menu (1-6): ").strip()

        if choice == "1":
            # Enroll mode with NIS
            print("\n[*] ENROLLMENT - Pendaftaran Wajah Orang Tua")
            print("=" * 50)

            # Input NIS
            nis = input("NIS Siswa: ").strip()
            if not nis:
                print("[X] NIS tidak boleh kosong!")
                continue

            # Lookup student dari database
            from backend.database.student_db import StudentDatabase
            student_db = StudentDatabase()

            student = student_db.get_student(nis)
            if not student:
                print(f"[X] NIS '{nis}' tidak ditemukan di database!")
                print("    Silakan hubungi admin untuk menambahkan data siswa.")
                continue

            # Display student info
            print(f"\n[*] Data Siswa Ditemukan:")
            print(f"   NIS: {student['nis']}")
            print(f"   Nama: {student['nama']}")
            print(f"   Kelas: {student['kelas']}")

            # Input parent name
            parent_name = input("\nNama Orang Tua: ").strip()
            if not parent_name:
                print("[X] Nama orang tua tidak boleh kosong!")
                continue

            # Confirm
            print(f"\n[*] Data yang akan didaftarkan:")
            print(f"   Orang Tua: {parent_name}")
            print(f"   Anak: {student['nama']} (NIS: {nis})")
            print(f"   Kelas: {student['kelas']}")

            confirm = input(
                "\nApakah data sudah benar? (y/n): ").strip().lower()
            if confirm != 'y':
                print("[!] Pendaftaran dibatalkan.")
                continue

            print(f"\n[*] Mode Enroll untuk: {parent_name}")
            print(f"   Anak: {student['nama']} ({student['kelas']})")
            capture_mode = "otomatis" if AUTO_CAPTURE_ENROLL else "manual"
            print(f"   Instruksi:")
            print(f"   - Posisikan wajah di depan kamera")
            print(f"   - Mode capture: {capture_mode}")
            print(f"   - Tekan 'c' untuk capture manual")
            print(f"   - Tekan 'q' untuk batal")
            print(f"   - Camera: {CAM_INDEX}\n")

            input("Tekan ENTER untuk mulai...")

            try:
                # Enroll face (returns embedding index)
                temp_label = f"{parent_name}_{student['nama']}_{student['kelas']}"

                embedding_index = enroll_mode(
                    app=app,
                    db=db,
                    name=temp_label,
                    cam_index=CAM_INDEX,
                    width=WIDTH,
                    height=HEIGHT,
                    samples=SAMPLES,
                    min_det_score=MIN_DET_SCORE,
                    mirror_camera=MIRROR_CAMERA,
                    save_snapshots=True,
                    auto_capture=AUTO_CAPTURE_ENROLL,
                    capture_interval=AUTO_CAPTURE_INTERVAL
                )

                # Check if enrollment succeeded
                if embedding_index is None:
                    print("\n[X] Enrollment gagal atau dibatalkan.")
                    continue

                # Save to student database
                try:
                    student_db.add_parent(nis, parent_name, embedding_index)
                except Exception as db_err:
                    # Rollback: hapus embedding yang sudah tersimpan
                    db.remove(embedding_index)
                    logger.log_error(
                        "EnrollRollback",
                        f"MySQL insert gagal, embedding {embedding_index} di-rollback: {db_err}",
                    )
                    print(f"\n[X] Gagal simpan ke database: {db_err}")
                    print(f"    Embedding telah di-rollback.")
                    input("\nTekan ENTER untuk kembali...")
                    continue
                print(f"\n[OK] Data orang tua disimpan ke database!")

                # Generate QR code with NIS (only once per student)
                if QR_AVAILABLE:
                    try:
                        qr_path = app_path("data", "qr_codes", f"{nis}.png")
                        if not os.path.exists(qr_path):
                            # Generate QR (first enrollment for this student)
                            qr_manager = QRCodeManager(
                                db_dir=app_path("data", "face_db"),
                                qr_dir=app_path("data", "qr_codes"))
                            qr_manager.generate_qr_code(nis, silent=False)
                            print(
                                f"\n[OK] QR code untuk siswa ini telah di-generate.")
                            print(f"     File: {nis}.png")
                            print(
                                f"     QR ini bisa digunakan oleh semua orang tua siswa ini.")
                        else:
                            print(f"\n[!] QR code sudah ada: {nis}.png")
                            print(
                                f"     QR ini bisa digunakan oleh semua orang tua siswa ini.")
                    except Exception as e:
                        print(f"[!] QR generation error: {e}")

            except Exception as e:
                print(f"\n[X] Error saat enrollment: {e}")
                logger.log_error("EnrollmentError", str(e))
                input("\nTekan ENTER untuk kembali...")

        elif choice == "2":
            # Recognize mode
            print("\n[*] Mode Recognize")
            print("   Instruksi:")
            print("   - Arahkan wajah ke kamera")
            print("   - Sistem akan otomatis mengenali wajah")
            print("   - Tekan 'q' untuk keluar")
            print(f"   - Camera: {CAM_INDEX}\n")

            input("Tekan ENTER untuk mulai...")

            try:
                recognize_mode(
                    app=app,
                    db=db,
                    cam_index=CAM_INDEX,
                    width=WIDTH,
                    height=HEIGHT,
                    threshold=THRESHOLD,
                    min_det_score=MIN_DET_SCORE,
                    show_performance=SHOW_PERFORMANCE,
                    mirror_camera=MIRROR_CAMERA
                )
            except Exception as e:
                print(f"\n[X] Error saat recognition: {e}")
                logger.log_error("RecognitionError", str(e))
                input("\nTekan ENTER untuk kembali...")

        elif choice == "3":
            # Switch Camera
            old_index = CAM_INDEX
            CAM_INDEX = switch_camera(CAM_INDEX, WIDTH, HEIGHT, MIRROR_CAMERA)
            if old_index != CAM_INDEX:
                settings["cam_index"] = CAM_INDEX
                save_settings(settings)
                logger.log_camera_switch(old_index, CAM_INDEX)

        elif choice == "4":
            # QR Code Menu
            if QR_AVAILABLE:
                qr_code_menu(CAM_INDEX, MIRROR_CAMERA)
            else:
                print("\n[X] QR Code feature tidak tersedia!")
                print("    Pyzbar DLL (libiconv.dll) tidak ditemukan.")
                print("    Program tetap bisa digunakan tanpa fitur QR code.")
                input("\nTekan ENTER untuk kembali...")

        elif choice == "5":
            action = settings_menu(settings)
            CAM_INDEX = settings["cam_index"]
            WIDTH = settings["width"]
            HEIGHT = settings["height"]
            DET_SIZE = settings["det_size"]
            MIN_DET_SCORE = settings["min_det_score"]
            SAMPLES = settings["samples"]
            THRESHOLD = settings["threshold"]
            SHOW_PERFORMANCE = settings["show_performance"]
            MIRROR_CAMERA = settings["mirror_camera"]

            if action == "reload_model":
                print("\n[*] Reload model dengan settings baru...")
                try:
                    app = build_face_app(
                        model_name=MODEL_NAME,
                        det_size=DET_SIZE,
                        device=DEVICE)
                    logger.log_model_load(MODEL_NAME, DEVICE, success=True)
                    print("[OK] Model berhasil di-reload!")
                except Exception as e:
                    logger.log_model_load(MODEL_NAME, DEVICE, success=False)
                    logger.log_error("ModelReloadError", str(e))
                    print(f"[X] Gagal reload model: {e}")
                    input("\nTekan ENTER untuk kembali...")

        elif choice == "6":
            print("\n[*] Terima kasih! Keluar dari program...")
            logger.log_system("System shutdown")
            sys.exit(0)

        else:
            print("\n[X] Pilihan tidak valid! Silakan pilih 1-6.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        import uvicorn
        sys.argv = [sys.argv[0]]  # reset argv for uvicorn
        port = os.getenv("SAFEPICK_WEB_PORT", os.getenv("FACEGATE_WEB_PORT", "8000"))
        uvicorn.run("backend.app:app", host="0.0.0.0", port=int(port))
    else:
        try:
            main()
        except KeyboardInterrupt:
            print("\n\n[*] Program dihentikan oleh user. Bye!")
            logger.log_system("System interrupted by user")
            sys.exit(0)
        except Exception as e:
            print(f"\n[X] Error: {e}")
            logger.log_error("SystemError", str(e), context="main")
            sys.exit(1)
