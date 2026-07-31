import io
import os
import shutil
import sys
import time
import argparse
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from cryptography.fernet import Fernet, InvalidToken

# InsightFace
from insightface.app import FaceAnalysis

# Logger
from backend.utils.logger import get_logger

# Performance Monitor
try:
    from backend.utils.monitor import PerformanceMonitor
    PERF_MONITOR_AVAILABLE = True
except ImportError:
    PERF_MONITOR_AVAILABLE = False
    PerformanceMonitor = None

logger = get_logger()


# =========================
# Utils
# =========================

def l2_normalize(x: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    n = np.linalg.norm(x) + eps
    return x / n


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    # a, b already normalized
    return float(np.dot(a, b))


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def now_str() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def ensure_opencv_gui() -> None:
    """Raise a clear error when OpenCV is installed without HighGUI support."""
    build_info = cv2.getBuildInformation()
    for line in build_info.splitlines():
        if line.strip().startswith("GUI:"):
            gui_backend = line.split(":", 1)[1].strip()
            if gui_backend.upper() == "NONE":
                raise RuntimeError(
                    "OpenCV terpasang tanpa GUI support (GUI: NONE). "
                    "Install paket GUI, misalnya uninstall 'opencv-python-headless' "
                    "lalu install ulang 'opencv-python'.")
            return


# =========================
# DB Storage
# =========================

@dataclass
class FaceDB:
    """
    Penyimpanan embedding wajah terenkripsi di disk.

    `embeddings.npy` saat ini berisi blob Fernet (AES128-CBC + HMAC-SHA256)
    yang membungkus dump NumPy biasa. Kunci disimpan di `.embeddings_key`
    di folder yang sama. File lama (plaintext `.npy`) otomatis dimigrasi
    saat instance pertama dibuat, dan versi sebelum migrasi disimpan
    sebagai `embeddings.npy.bak` untuk recovery manual jika perlu.
    """

    db_dir: str
    emb_path: str

    def __init__(self, db_dir: str = "data/face_db"):
        self.db_dir = db_dir
        ensure_dir(db_dir)
        self.emb_path = os.path.join(db_dir, "embeddings.npy")
        self.key_path = os.path.join(db_dir, ".embeddings_key")
        self._cipher = self._load_or_create_key()
        self._maybe_migrate_plaintext()

    # ---- Kunci enkripsi --------------------------------------------------

    def _load_or_create_key(self) -> Fernet:
        if os.path.exists(self.key_path):
            with open(self.key_path, "rb") as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_path, "wb") as f:
                f.write(key)
            # POSIX: kunci hanya dapat dibaca pemilik proses.
            if os.name != "nt":
                try:
                    os.chmod(self.key_path, 0o600)
                except OSError:
                    pass
            print(f"[*] Kunci enkripsi embedding dibuat: {self.key_path}")
        return Fernet(key)

    # ---- Serialisasi -----------------------------------------------------

    def _serialize(self, embs: np.ndarray) -> bytes:
        buf = io.BytesIO()
        np.save(buf, np.ascontiguousarray(embs.astype(np.float32)),
                allow_pickle=False)
        return self._cipher.encrypt(buf.getvalue())

    def _deserialize(self, blob: bytes) -> np.ndarray:
        plain = self._cipher.decrypt(blob)
        return np.load(io.BytesIO(plain), allow_pickle=False).astype(np.float32)

    @staticmethod
    def _looks_plaintext_numpy(path: str) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(6)
        except OSError:
            return False
        # Magic number `.npy`: \x93NUMPY. Fernet token diawali "gAAAAA"
        # (base64 dari version byte 0x80 + timestamp 0x00000000_xxxxxxxx).
        return head[:6] == b"\x93NUMPY"

    def _write_atomic(self, blob: bytes) -> None:
        tmp = self.emb_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, self.emb_path)

    # ---- Migrasi otomatis ------------------------------------------------

    def _maybe_migrate_plaintext(self) -> None:
        if not os.path.exists(self.emb_path):
            return
        if not self._looks_plaintext_numpy(self.emb_path):
            return  # sudah terenkripsi atau format tidak dikenali

        try:
            arr = np.load(self.emb_path, allow_pickle=False).astype(np.float32)
        except Exception as exc:
            print(f"Warning: gagal baca embeddings.npy plaintext "
                  f"untuk migrasi: {exc}")
            return

        backup = self.emb_path + ".bak"
        try:
            shutil.copy2(self.emb_path, backup)
        except OSError as exc:
            print(f"Warning: gagal buat backup {backup}: {exc}")

        try:
            self._write_atomic(self._serialize(arr))
            print(f"[*] embeddings.npy berhasil dimigrasi ke format "
                  f"terenkripsi Fernet (backup plaintext: {backup}).")
        except Exception as exc:
            print(f"Warning: gagal tulis embeddings.npy terenkripsi: {exc}")

    # ---- API publik ------------------------------------------------------

    def load_raw(self) -> np.ndarray:
        """Baca matriks embedding mentah (belum di-L2-normalize)."""
        if not os.path.exists(self.emb_path):
            return np.zeros((0, 512), dtype=np.float32)
        try:
            with open(self.emb_path, "rb") as f:
                blob = f.read()
            embs = self._deserialize(blob)
            if embs is None or len(embs) == 0:
                return np.zeros((0, 512), dtype=np.float32)
            return embs
        except InvalidToken:
            print("Warning: embeddings.npy tidak bisa didekripsi "
                  "(kunci salah atau file rusak). Mengembalikan array kosong.")
            return np.zeros((0, 512), dtype=np.float32)
        except Exception as exc:
            print(f"Warning: gagal baca embeddings ({exc}). Mengembalikan array kosong.")
            return np.zeros((0, 512), dtype=np.float32)

    def save_raw(self, embs: np.ndarray) -> None:
        """Tulis matriks embedding mentah dalam bentuk terenkripsi."""
        if embs is None or len(embs) == 0:
            if os.path.exists(self.emb_path):
                os.remove(self.emb_path)
            return
        self._write_atomic(self._serialize(embs))

    def load(self) -> np.ndarray:
        """Load + L2-normalize tiap baris (interface lama, dipakai recognize)."""
        embs = self.load_raw()
        if len(embs) == 0:
            return embs
        return np.stack([l2_normalize(e) for e in embs], axis=0)

    def add(self, emb: np.ndarray) -> int:
        """
        Tambah satu embedding (otomatis L2-normalize) ke matriks dan
        kembalikan index barunya.
        """
        embs = self.load()
        emb = l2_normalize(emb.astype(np.float32))

        if len(embs) == 0:
            new_embs = emb.reshape(1, -1)
            index = 0
        else:
            if emb.shape[0] != embs.shape[1]:
                raise ValueError(
                    f"Embedding dim mismatch: got {emb.shape[0]}, "
                    f"expected {embs.shape[1]}")
            new_embs = np.vstack([embs, emb.reshape(1, -1)])
            index = len(embs)

        self.save_raw(new_embs)
        return index

    def remove(self, index: int) -> bool:
        """
        Hapus baris pada `index` dari matriks.

        NOTE: Operasi ini menggeser semua index setelahnya turun satu.
        Hanya pakai segera setelah `add()` gagal sebagai rollback;
        untuk operasi runtime gunakan `tombstone()` agar mapping
        `parents.embedding_index` di MySQL tetap valid.
        """
        if not os.path.exists(self.emb_path):
            return False

        embs = self.load_raw()
        if embs.ndim < 2 or index < 0 or index >= len(embs):
            return False

        new_embs = np.delete(embs, index, axis=0)
        self.save_raw(new_embs)
        return True

    def tombstone(self, index: int) -> bool:
        """
        Zero-out embedding di posisi `index` tanpa menggeser baris lain.
        Cosine similarity dengan input apa pun = 0 -> tidak akan menang
        argmax dan pasti di bawah threshold default 0.35.
        """
        if not os.path.exists(self.emb_path):
            return False

        embs = self.load_raw()
        if embs.ndim < 2 or index < 0 or index >= len(embs):
            return False

        embs[index] = 0.0
        self.save_raw(embs)
        return True


# =========================
# InsightFace App
# =========================

def build_face_app(
        model_name: str = "buffalo_l",
        det_size: int = 640,
        device: str = "cpu",
        model_root: str = None) -> FaceAnalysis:
    """
    Load InsightFace model dari folder lokal project.

    Args:
        model_name: nama model (default: "buffalo_l")
        det_size: ukuran detection (default: 640)
        device: "cpu" atau "cuda"
        model_root: path ke folder root. Jika None, akan gunakan direktori script
                   (InsightFace akan otomatis menambahkan subfolder 'models')

    Returns:
        FaceAnalysis app yang sudah di-prepare
    """
    # Tentukan model root path
    if model_root is None:
        # Check if running from PyInstaller
        if getattr(sys, 'frozen', False):
            # Running from PyInstaller EXE
            # sys.executable points to the EXE file
            # We want the directory containing the EXE
            model_root = os.path.dirname(sys.executable)
        else:
            # Running from Python script
            model_root = os.path.dirname(os.path.abspath(__file__))

    # InsightFace akan cari di: model_root/models/model_name
    expected_model_path = os.path.join(model_root, "models", model_name)

    # Jika tidak ditemukan, coba cari di parent directory (untuk compatibility)
    if not os.path.exists(expected_model_path):
        current_check = model_root
        for _ in range(3):  # Coba naik hingga 3 level folder
            parent_root = os.path.dirname(current_check)
            alternative_path = os.path.join(parent_root, "models", model_name)
            if os.path.exists(alternative_path):
                model_root = parent_root
                expected_model_path = alternative_path
                break
            current_check = parent_root

    if not os.path.exists(expected_model_path):
        raise FileNotFoundError(
            f"Model tidak ditemukan di: {expected_model_path}\n"
            f"Pastikan folder 'models/{model_name}' ada dan berisi file .onnx\n"
            f"Model root: {model_root}\n"
            f"Coba copy folder 'models' ke: {model_root}")

    print(f"Loading model dari: {expected_model_path}")

    # Setup providers
    providers = None
    if device.lower() == "cuda":
        # kalau kamu pakai onnxruntime-gpu
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    else:
        providers = ["CPUExecutionProvider"]

    # Load model dari folder lokal
    # root parameter akan membuat InsightFace cari di <root>/models/<name>
    app = FaceAnalysis(name=model_name, root=model_root, providers=providers)
    app.prepare(ctx_id=0 if device.lower() == "cuda" else -1,
                det_size=(det_size, det_size))
    return app


def pick_largest_face(faces) -> Optional[object]:
    if not faces:
        return None
    # pilih wajah terbesar (asumsi subjek utama)
    areas = []
    for f in faces:
        x1, y1, x2, y2 = f.bbox.astype(int)
        areas.append((x2 - x1) * (y2 - y1))
    return faces[int(np.argmax(areas))]


# =========================
# Camera Loop
# =========================

def open_camera(cam_index: int, width: int, height: int) -> cv2.VideoCapture:
    """
    Open camera with backend fallbacks.

    On Windows, some cameras only switch reliably when forced through a
    specific backend, so try a short backend chain instead of CAP_ANY only.
    """
    backends = [None]
    if os.name == "nt":
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, None]

    errors = []
    for backend in backends:
        cap = cv2.VideoCapture(
            cam_index) if backend is None else cv2.VideoCapture(
            cam_index, backend)

        if not cap.isOpened():
            backend_name = "CAP_ANY" if backend is None else str(backend)
            errors.append(f"{backend_name}: open failed")
            cap.release()
            continue

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        # Warm up a few frames so Windows drivers finish switching devices.
        opened = False
        for _ in range(5):
            ok, _ = cap.read()
            if ok:
                opened = True
                break

        if opened:
            return cap

        backend_name = "CAP_ANY" if backend is None else str(backend)
        errors.append(f"{backend_name}: frame read failed")
        cap.release()

    raise RuntimeError(
        f"Camera {cam_index} cannot be opened. Tried backends: {', '.join(errors)}")


def draw_box_and_text(img, face, text: str):
    """Draw face scanner corner brackets and compact label badge."""
    x1, y1, x2, y2 = face.bbox.astype(int)
    h, w = img.shape[:2]
    x1 = max(0, min(w - 1, x1))
    y1 = max(0, min(h - 1, y1))
    x2 = max(0, min(w - 1, x2))
    y2 = max(0, min(h - 1, y2))

    color = (46, 180, 141) if "Unknown" not in text else (0, 122, 255)
    thickness = 6 if w >= 1280 else 4
    corner = max(30, min(84, int(min(x2 - x1, y2 - y1) * 0.32)))
    for sx, sy, ex, ey in (
        (x1, y1, x1 + corner, y1),
        (x1, y1, x1, y1 + corner),
        (x2, y1, x2 - corner, y1),
        (x2, y1, x2, y1 + corner),
        (x1, y2, x1 + corner, y2),
        (x1, y2, x1, y2 - corner),
        (x2, y2, x2 - corner, y2),
        (x2, y2, x2, y2 - corner),
    ):
        cv2.line(img, (sx, sy), (ex, ey), color, thickness, cv2.LINE_AA)

    label_text = str(text)
    if "\n" in label_text:
        lines = [line.strip() for line in label_text.splitlines() if line.strip()]
        label_text = lines[1] if len(lines) > 1 else lines[0]
        if len(lines) > 2:
            label_text = f"{label_text} | {lines[2]}"
    elif "_" in label_text and label_text.count("_") >= 2:
        parts = label_text.split("|")[0].strip().split("_")
        if len(parts) >= 3:
            label_text = f"{parts[1]} | {parts[2]}"

    label = label_text.split("|")[0].strip()
    if len(label) > 38:
        label = f"{label[:35]}..."

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = max(1.2, min(1.8, w / 1140.0))
    font_thickness = 5 if w >= 1280 else 4
    text_size, baseline = cv2.getTextSize(label, font, font_scale, font_thickness)
    badge_w = min(w - 8, text_size[0] + 44)
    badge_h = text_size[1] + baseline + 30
    bx1 = max(4, min(x1, w - badge_w - 4))
    by1 = y2 + 8
    if by1 + badge_h > h - 4:
        by1 = max(4, y1 - badge_h - 8)
    bx2 = bx1 + badge_w
    by2 = by1 + badge_h

    cv2.rectangle(img, (bx1, by1), (bx2, by2), (8, 15, 20), -1, cv2.LINE_AA)
    cv2.rectangle(img, (bx1, by1), (bx2, by2), color, 2, cv2.LINE_AA)
    cv2.putText(
        img,
        label,
        (bx1 + 22, by2 - baseline - 12),
        font,
        font_scale,
        (245, 255, 248),
        font_thickness,
        cv2.LINE_AA,
    )


# =========================
# Modes
# =========================

def enroll_mode(app: FaceAnalysis,
                db: FaceDB,
                name: str,
                cam_index: int = 0,
                width: int = 1280,
                height: int = 720,
                samples: int = 10,
                min_det_score: float = 0.6,
                mirror_camera: bool = False,
                save_snapshots: bool = True,
                auto_capture: bool = False,
                capture_interval: float = 1.2):
    """
    Ambil beberapa sample embedding lalu rata-ratakan -> satu embedding per orang (lebih stabil).
    Tekan:
      - 'c' capture sample manual
      - 'q' quit
    """
    snap_dir = os.path.join(db.db_dir, "snapshots", name.replace(" ", "_"))
    if save_snapshots:
        ensure_dir(snap_dir)

    ensure_opencv_gui()
    cap = open_camera(cam_index, width, height)
    collected = []
    last_capture_time = 0.0

    print("\n[ENROLL]")
    mode_text = "otomatis" if auto_capture else "manual"
    print(f"Arahkan wajah ke kamera. Mode capture: {mode_text}.")
    print("Tekan 'c' untuk capture manual, atau 'q' untuk selesai.")
    print(f"Target samples: {samples}.\n")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Gagal baca frame.")
            break
        if mirror_camera:
            frame = cv2.flip(frame, 1)

        faces = app.get(frame)
        face = pick_largest_face(faces)

        disp = frame.copy()
        mode_label = "AUTO" if auto_capture else "MANUAL"
        if face is not None and float(face.det_score) >= min_det_score:
            draw_box_and_text(
                disp,
                face,
                f"{name} | {mode_label} | det={face.det_score:.2f} | samples={len(collected)}/{samples}")
        else:
            cv2.putText(disp, "No face / low confidence", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        cv2.imshow("Enroll - auto/manual capture, q to quit", disp)
        key = cv2.waitKey(1) & 0xFF
        now = time.time()
        should_capture = key == ord('c')
        if (auto_capture and face is not None
                and float(face.det_score) >= min_det_score
                and now - last_capture_time >= capture_interval):
            should_capture = True
            last_capture_time = now

        if should_capture:
            if face is None or float(face.det_score) < min_det_score:
                print(
                    "Wajah belum terdeteksi jelas. Coba lagi (lebih dekat / pencahayaan lebih terang).")
                continue

            emb = face.normed_embedding  # biasanya sudah L2 normalized
            collected.append(emb.astype(np.float32))

            if save_snapshots:
                fn = os.path.join(
                    snap_dir, f"{now_str()}_{len(collected)}.jpg")
                cv2.imwrite(fn, frame)

            print(f"Captured sample {len(collected)}/{samples}")

            if len(collected) >= samples:
                break

        elif key == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

    if len(collected) == 0:
        print("Tidak ada sample. Enroll dibatalkan.")
        logger.log_enrollment(name, 0, success=False, camera_index=cam_index,
                              notes="No samples collected")
        return

    # rata-rata lalu normalize
    avg_emb = np.mean(np.stack(collected, axis=0), axis=0)
    avg_emb = l2_normalize(avg_emb)

    # Add to FaceDB and get index
    index = db.add(avg_emb)
    logger.log_enrollment(
        name,
        len(collected),
        success=True,
        camera_index=cam_index)
    print(
        f"\n✅ Enroll selesai. '{name}' ditambahkan ke database ({db.db_dir}).")
    print(f"   Embedding index: {index}")

    return index  # Return index for database save


def recognize_mode(app: FaceAnalysis,
                   db: FaceDB,
                   cam_index: int = 0,
                   width: int = 1280,
                   height: int = 720,
                   threshold: float = 0.35,
                   min_det_score: float = 0.6,
                   show_performance: bool = True,
                   mirror_camera: bool = False):
    """
    Real-time recognition:
    - ambil embedding wajah terbesar
    - hitung cosine similarity ke semua embedding di DB
    - jika sim >= threshold => dikenal
    - lookup database untuk get student info
    Catatan:
    - threshold perlu dikalibrasi (0.3 - 0.5 tergantung model & kondisi).
    """
    embs = db.load()

    # Check if database is empty or invalid
    if embs is None or len(embs) == 0:
        print("\n[X] Database wajah masih kosong.")
        print(f"    File embedding belum ada di: {db.db_dir}")
        print("    Jalankan menu Enroll terlebih dahulu sebelum Recognize.")
        input("\nTekan ENTER untuk kembali ke menu...")
        return

    # Load student database
    from backend.database.student_db import StudentDatabase
    student_db = StudentDatabase()

    ensure_opencv_gui()
    cap = open_camera(cam_index, width, height)
    print("\n[RECOGNIZE]")
    print("Tekan 'q' untuk keluar.")
    if show_performance and PERF_MONITOR_AVAILABLE:
        print("Tekan 'p' untuk toggle performance stats.")
        print("Performance akan di-log ke: logs/performance.log")
    print()

    # Initialize performance monitor
    perf_monitor = PerformanceMonitor() if PERF_MONITOR_AVAILABLE else None
    show_perf_overlay = show_performance and PERF_MONITOR_AVAILABLE

    # Initialize performance logger
    perf_logger = None
    if PERF_MONITOR_AVAILABLE:
        try:
            from backend.utils.monitor import PerformanceLogger
            perf_logger = PerformanceLogger("logs/performance.log")
        except Exception as e:
            print(f"[!] Performance logging disabled: {e}")

    frame_count = 0
    last_result = None  # Cache last recognition result

    while True:
        # Start frame timing
        if perf_monitor:
            perf_monitor.start_frame()

        ok, frame = cap.read()
        if not ok:
            print("Gagal baca frame.")
            break
        if mirror_camera:
            frame = cv2.flip(frame, 1)

        frame_count += 1
        disp = frame.copy()

        # Process every 3rd frame for better performance
        # Display cached result on skipped frames
        if frame_count % 3 == 0:
            # Time inference (always record, even if no faces found)
            inference_start = time.perf_counter()

            faces = app.get(frame)

            if perf_monitor:
                inference_time = time.perf_counter() - inference_start
                perf_monitor.record_inference_time(inference_time)

            # Process faces with SMART MODE (adaptive)
            recognized_faces = []

            if faces and len(faces) > 0:
                # SMART MODE LOGIC:
                # - 1 face: Process 1 (fastest)
                # - 2-3 faces: Process all (useful for parent+child)
                # - 4+ faces: Process top 3 only (performance balance)

                num_faces = len(faces)

                if num_faces == 1:
                    # Single face: Process immediately (fastest path)
                    faces_to_process = faces
                    mode_text = "FAST"
                elif num_faces <= 3:
                    # 2-3 faces: Process all (parent+child scenario)
                    faces_to_process = faces
                    mode_text = "ALL"
                else:
                    # 4+ faces: Process top 3 largest (performance balance)
                    # Sort by face area (largest first)
                    faces_sorted = sorted(faces, key=lambda f: (
                        f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
                    faces_to_process = faces_sorted[:3]
                    mode_text = "TOP3"

                # Process selected faces
                for face in faces_to_process:
                    if float(face.det_score) >= min_det_score:
                        emb = face.normed_embedding.astype(np.float32)
                        emb = l2_normalize(emb)

                        # cosine similarity: embs already normalized
                        sims = embs @ emb  # (N,)
                        best_idx = int(np.argmax(sims))
                        best_sim = float(sims[best_idx])

                        if best_sim >= threshold:
                            # Lookup database by embedding index
                            parent = student_db.get_parent_by_index(best_idx)

                            if parent:
                                # Format: "Ortu: [Nama] | Anak: [Nama] ([Kelas])"
                                name = f"Ortu: {parent['nama_ortu']} | Anak: {parent['nama_anak']} ({parent['kelas']})"
                                recognized_faces.append(
                                    (face, f"{name} | sim={best_sim:.2f}", True))
                                logger.log_recognition(
                                    parent['nama_ortu'], best_sim, cam_index, threshold)
                            else:
                                # Index found but no database entry (data
                                # mismatch)
                                name = f"Index:{best_idx} (No DB entry)"
                                recognized_faces.append(
                                    (face, f"{name} | sim={best_sim:.2f}", False))
                                logger.log_recognition(
                                    None, best_sim, cam_index, threshold)
                        else:
                            recognized_faces.append(
                                (face, f"Unknown | sim={best_sim:.2f}", False))
                            logger.log_recognition(
                                None, best_sim, cam_index, threshold)

                # Store mode info for display
                if recognized_faces:
                    # Add mode info to first face for display
                    face_info = recognized_faces[0]
                    recognized_faces[0] = (
                        face_info[0],
                        face_info[1],
                        face_info[2],
                        mode_text,
                        num_faces)

            # Cache results for all faces
            last_result = recognized_faces if recognized_faces else None

        # Draw all recognized faces (even on skipped frames for smooth display)
        if last_result is not None and len(last_result) > 0:
            # Extract mode info if available (from first face)
            mode_text = None
            total_detected = None

            for idx, face_data in enumerate(last_result):
                # Handle both old format (3 items) and new format (5 items)
                if len(face_data) == 5:
                    face, text, _, mode, total = face_data
                    if idx == 0:  # Only get mode from first face
                        mode_text = mode
                        total_detected = total
                else:
                    face, text, _ = face_data

                draw_box_and_text(disp, face, text)

            # Add Smart Mode indicator
            if mode_text and total_detected:
                mode_display = f"Mode: {mode_text} ({len(last_result)}/{total_detected})"
                cv2.putText(
                    disp,
                    mode_display,
                    (disp.shape[1] -
                     250,
                     30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0,
                     255,
                     255),
                    2,
                    cv2.LINE_AA)
            else:
                # Fallback: just show count
                face_count_text = f"Faces: {len(last_result)}"
                cv2.putText(
                    disp,
                    face_count_text,
                    (disp.shape[1] -
                     150,
                     30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0,
                     255,
                     255),
                    2,
                    cv2.LINE_AA)
        else:
            cv2.putText(disp, "No face / low confidence", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        # End frame timing BEFORE overlay draw so rendering doesn't inflate
        # metrics
        if perf_monitor:
            perf_monitor.end_frame()

            # Log performance every 30 frames
            if perf_logger and frame_count % 30 == 0:
                try:
                    perf_logger.log(perf_monitor)
                except Exception as e:
                    pass  # Silent fail for logging

        # Draw performance overlay (after end_frame so stats are fresh)
        if show_perf_overlay and perf_monitor:
            stats_text = perf_monitor.get_stats_string()
            # Background for better readability
            cv2.rectangle(
                disp, (5, disp.shape[0] - 35), (disp.shape[1] - 5, disp.shape[0] - 5), (0, 0, 0), -1)
            cv2.putText(
                disp,
                stats_text,
                (10,
                 disp.shape[0] -
                    15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0,
                 255,
                 0),
                1,
                cv2.LINE_AA)

        cv2.imshow("Recognize - press q to quit", disp)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('p') and perf_monitor:
            # Toggle performance overlay
            show_perf_overlay = not show_perf_overlay

    cap.release()
    cv2.destroyAllWindows()

    # Print final stats and stop background thread
    if perf_monitor:
        print("\n[*] Performance Summary:")
        perf_monitor.print_stats()
        perf_monitor.stop()


# =========================
# Main
# =========================

def main():
    parser = argparse.ArgumentParser(
        description="Face recognition using InsightFace (ArcFace embeddings).")
    parser.add_argument(
        "--mode",
        choices=[
            "enroll",
            "recognize"],
        required=True)
    parser.add_argument(
        "--name",
        type=str,
        default="",
        help="Name/ID for enroll")
    parser.add_argument("--db", type=str, default="face_db", help="DB folder")
    parser.add_argument(
        "--model",
        type=str,
        default="buffalo_sc",
        help="InsightFace model pack name (e.g., buffalo_sc)")
    parser.add_argument(
        "--device",
        type=str,
        default="cpu",
        choices=[
            "cpu",
            "cuda"],
        help="Runtime device")
    parser.add_argument("--cam", type=int, default=1, help="Camera index")
    parser.add_argument("--w", type=int, default=1280)
    parser.add_argument("--h", type=int, default=720)
    parser.add_argument(
        "--det",
        type=int,
        default=640,
        help="det_size (square)")
    parser.add_argument(
        "--min_det",
        type=float,
        default=0.6,
        help="Minimum detection score")
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="Enroll samples")
    parser.add_argument(
        "--thr",
        type=float,
        default=0.35,
        help="Cosine similarity threshold for 'known'")
    args = parser.parse_args()

    db = FaceDB(args.db)
    app = build_face_app(
        model_name=args.model,
        det_size=args.det,
        device=args.device)

    if args.mode == "enroll":
        if not args.name.strip():
            raise ValueError(
                "Mode enroll but --name is empty. Example: --name \"Raihan\"")
        enroll_mode(app, db, name=args.name.strip(),
                    cam_index=args.cam, width=args.w, height=args.h,
                    samples=args.samples, min_det_score=args.min_det)
    else:
        recognize_mode(app, db,
                       cam_index=args.cam, width=args.w, height=args.h,
                       threshold=args.thr, min_det_score=args.min_det)


if __name__ == "__main__":
    main()
