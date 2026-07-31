#!/usr/bin/env python3
"""
FastAPI web UI for SafePick.

The web layer stays separate from the existing CLI entry point and reuses the
core modules under src/.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np
import psutil
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pyzbar.pyzbar import decode
from pydantic import BaseModel, Field

try:
    from gtts import gTTS
except ImportError:  # pragma: no cover - handled at runtime
    gTTS = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.face_engine import (  # noqa: E402
    FaceDB,
    build_face_app,
    draw_box_and_text,
    l2_normalize,
    open_camera,
    pick_largest_face,
)
from backend.core.qr_manager import QRCodeManager  # noqa: E402
from backend.database.student_db import StudentDatabase  # noqa: E402
from backend.ui.dashboard import AdminAccountStore, serialize_log_rows  # noqa: E402
from backend.hardware.led import get_led, shutdown_led  # noqa: E402
from backend.utils.logger import get_app_logger  # noqa: E402

APP_ROOT = PROJECT_ROOT
SETTINGS_PATH = APP_ROOT / "data" / "settings.json"
FACE_DB_DIR = APP_ROOT / "data" / "face_db"
QR_DIR = APP_ROOT / "data" / "qr_codes"
ATTENDANCE_PHOTO_DIR = APP_ROOT / "data" / "attendance_photos"
UNKNOWN_PHOTO_DIR = APP_ROOT / "data" / "unknown_photos"
TTS_CACHE_DIR = APP_ROOT / "data" / "tts_cache"
SESSION_COOKIE = "safepick_admin_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
ATTENDANCE_LOG_COOLDOWN_SECONDS = 60
UNKNOWN_FACE_COOLDOWN_SECONDS = 15


def _env(name: str, legacy_name: str, default: str) -> str:
    return os.getenv(name, os.getenv(legacy_name, default))


ATTENDANCE_PHOTO_RETENTION_DAYS = int(
    _env("SAFEPICK_ATTENDANCE_PHOTO_RETENTION_DAYS", "FACEGATE_ATTENDANCE_PHOTO_RETENTION_DAYS", "31")
)
RECOGNIZE_SLOW_FRAME_SECONDS = float(
    _env("SAFEPICK_RECOGNIZE_SLOW_FRAME_SECONDS", "FACEGATE_RECOGNIZE_SLOW_FRAME_SECONDS", "0.15")
)
RECOGNIZE_INFERENCE_WIDTH = int(
    _env("SAFEPICK_RECOGNIZE_INFERENCE_WIDTH", "FACEGATE_RECOGNIZE_INFERENCE_WIDTH", "640")
)
PARENT_CACHE_MAX_SIZE = int(_env("SAFEPICK_PARENT_CACHE_MAX_SIZE", "FACEGATE_PARENT_CACHE_MAX_SIZE", "128"))
_last_attendance_photo_cleanup_day = ""
app_logger = get_app_logger()
APP_STARTED_AT = time.time()
_client_error_times: Dict[str, deque] = {}
_client_error_lock = threading.Lock()
_voice_cache_batch_lock = threading.Lock()


def _prepare_inference_frame(frame: np.ndarray) -> tuple[np.ndarray, float]:
    """Downscale frame for faster inference if wider than RECOGNIZE_INFERENCE_WIDTH.

    Returns (inference_frame, scale) where scale is the multiplier to restore
    original-pixel bounding boxes (1.0 = no resize).
    """
    h, w = frame.shape[:2]
    if RECOGNIZE_INFERENCE_WIDTH <= 0 or w <= RECOGNIZE_INFERENCE_WIDTH:
        return frame, 1.0
    scale = w / float(RECOGNIZE_INFERENCE_WIDTH)
    infer_h = max(1, int(round(h / scale)))
    infer_frame = cv2.resize(
        frame,
        (RECOGNIZE_INFERENCE_WIDTH, infer_h),
        interpolation=cv2.INTER_AREA,
    )
    return infer_frame, scale


def app_path(*parts: str) -> Path:
    return APP_ROOT.joinpath(*parts)


def default_settings() -> Dict[str, Any]:
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
        # Template suara untuk skenario "anak dijemput" (face pickup & QR pickup).
        "voice_announcement_template": "Ananda {nama_anak} telah dijemput.",
        # Template suara untuk skenario "anak datang" (QR kehadiran pagi).
        "voice_announcement_attendance_template": "Ananda {nama_anak} telah hadir.",
        "threshold": 0.35,
        "show_performance": True,
        "mirror_camera": False,
    }


def load_settings() -> Dict[str, Any]:
    settings = default_settings()
    if SETTINGS_PATH.exists():
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            settings.update(json.load(f))
    return settings


def save_settings(settings: Dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_PATH.open("w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def db_call(fn):
    try:
        db = StudentDatabase()
        return {"ok": True, "data": fn(db)}
    except Exception as exc:
        app_logger.exception("database operation failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def primary_lan_ip() -> str:
    """
    Deteksi IP LAN utama untuk URL akses dari device lain di jaringan
    yang sama. Pakai UDP socket connect ke 8.8.8.8 untuk dapat IP
    interface default-route — tidak benar-benar kirim data ke Google,
    cuma tanya kernel mana interface yang akan dipakai.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def session_secret() -> str:
    secret = _env("SAFEPICK_SESSION_SECRET", "FACEGATE_SESSION_SECRET", "")
    if secret:
        return secret
    return f"{APP_ROOT}:{os.getenv('COMPUTERNAME', 'safepick')}:local-dashboard"


def sign_session(username: str, issued_at: int) -> str:
    payload = f"{username}:{issued_at}"
    signature = hmac.new(
        session_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("ascii")


def read_session(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, issued_at_text, signature = decoded.rsplit(":", 2)
        issued_at = int(issued_at_text)
    except Exception:
        return None

    if time.time() - issued_at > SESSION_TTL_SECONDS:
        return None

    expected = sign_session(username, issued_at)
    if not hmac.compare_digest(expected, token):
        return None
    return username


def require_admin(request: Request) -> str:
    username = read_session(request)
    if not username:
        raise HTTPException(status_code=401, detail="Login diperlukan.")
    return username


def embedding_info() -> Dict[str, Any]:
    emb_path = FACE_DB_DIR / "embeddings.npy"
    if not emb_path.exists():
        return {"exists": False, "rows": 0, "dim": 0, "path": str(emb_path)}

    # File embeddings sekarang terenkripsi Fernet; dekripsi lewat FaceDB.
    arr = FaceDB(str(FACE_DB_DIR)).load_raw()
    rows = int(arr.shape[0]) if arr.ndim >= 1 else 0
    dim = int(arr.shape[1]) if arr.ndim == 2 else 0
    return {
        "exists": True,
        "rows": rows,
        "dim": dim,
        "dtype": str(arr.dtype),
        "path": str(emb_path),
    }


def qr_count() -> int:
    if not QR_DIR.exists():
        return 0
    return len([p for p in QR_DIR.rglob("*.png") if p.is_file()])


def read_frame_from_camera(settings: Dict[str, Any]) -> np.ndarray:
    cap = open_camera(
        int(settings["cam_index"]),
        int(settings["width"]),
        int(settings["height"]),
    )
    try:
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("Gagal membaca frame dari kamera.")
        if settings.get("mirror_camera"):
            frame = cv2.flip(frame, 1)
        return frame
    finally:
        cap.release()


def encode_jpeg(frame: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        raise RuntimeError("Gagal encode frame JPEG.")
    return buffer.tobytes()


def mjpeg_part(frame: np.ndarray) -> bytes:
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n\r\n" + encode_jpeg(frame) + b"\r\n"
    )


def decode_qr_frame(frame: np.ndarray, manager: QRCodeManager) -> Optional[Dict[str, Any]]:
    objects = decode(frame)
    if not objects:
        return None

    qr_data = objects[0].data.decode("utf-8", errors="replace")
    return manager.resolve_qr_data(qr_data)


def draw_status_text(frame: np.ndarray, lines, ok: bool = True) -> None:
    color = (32, 220, 140) if ok else (0, 0, 255)
    for index, line in enumerate(lines):
        cv2.putText(
            frame,
            str(line),
            (15, 34 + (index * 30)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            color,
            2,
            cv2.LINE_AA,
        )


def draw_success_badge(frame: np.ndarray) -> None:
    h, w = frame.shape[:2]
    radius = 52
    cx = max(radius + 16, w - radius - 22)
    cy = radius + 22
    cv2.circle(frame, (cx, cy), radius, (32, 180, 90), -1, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), radius, (245, 255, 248), 5, cv2.LINE_AA)
    cv2.line(
        frame,
        (cx - 27, cy + 2),
        (cx - 9, cy + 22),
        (255, 255, 255),
        10,
        cv2.LINE_AA,
    )
    cv2.line(
        frame,
        (cx - 9, cy + 22),
        (cx + 31, cy - 24),
        (255, 255, 255),
        10,
        cv2.LINE_AA,
    )


def safe_filename_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value))
    return "_".join(part for part in cleaned.split("_") if part) or "UNKNOWN"


def attendance_photo_type_dir(jenis_absen: str) -> str:
    return {
        "KEHADIRAN_QR": "QR Kehadiran",
        "PENJEMPUTAN_QR": "QR Penjemputan",
        "PENJEMPUTAN_FACE": "Muka Penjemputan",
    }.get(jenis_absen, safe_filename_part(jenis_absen))


def cleanup_old_attendance_photos() -> None:
    global _last_attendance_photo_cleanup_day

    today = time.strftime("%Y-%m-%d")
    if _last_attendance_photo_cleanup_day == today:
        return
    _last_attendance_photo_cleanup_day = today

    if ATTENDANCE_PHOTO_RETENTION_DAYS <= 0 or not ATTENDANCE_PHOTO_DIR.exists():
        return

    cutoff = time.time() - (ATTENDANCE_PHOTO_RETENTION_DAYS * 24 * 60 * 60)
    for day_dir in ATTENDANCE_PHOTO_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            date_format = "%Y-%m-%d" if "-" in day_dir.name else "%Y%m%d"
            folder_time = time.mktime(time.strptime(day_dir.name, date_format))
        except ValueError:
            continue
        if folder_time < cutoff:
            shutil.rmtree(day_dir, ignore_errors=True)


def unique_photo_path(directory: Path, stem: str) -> Path:
    path = directory / f"{stem}.jpg"
    if not path.exists():
        return path

    index = 2
    while True:
        candidate = directory / f"{stem}_{index}.jpg"
        if not candidate.exists():
            return candidate
        index += 1


def save_attendance_photo(frame: np.ndarray, jenis_absen: str, nis: str,
                          student: Optional[Dict[str, Any]] = None) -> str:
    base_dir = UNKNOWN_PHOTO_DIR if jenis_absen == "UNKNOWN_FACE" else ATTENDANCE_PHOTO_DIR
    if base_dir == ATTENDANCE_PHOTO_DIR:
        cleanup_old_attendance_photos()

    type_dir = attendance_photo_type_dir(jenis_absen) if base_dir == ATTENDANCE_PHOTO_DIR else safe_filename_part(jenis_absen)
    day_dir = base_dir / time.strftime("%Y-%m-%d") / type_dir
    day_dir.mkdir(parents=True, exist_ok=True)

    nama = student.get("nama") if student else None
    kelas = student.get("kelas") if student else None
    stem = safe_filename_part(f"{nama or nis}_{kelas or 'UNKNOWN'}")
    path = unique_photo_path(day_dir, stem)
    ok = cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise RuntimeError("Gagal menyimpan bukti foto.")
    return path.relative_to(APP_ROOT).as_posix()


def record_attendance_evidence(student_db: Optional[StudentDatabase], key: str,
                               frame: np.ndarray, jenis_absen: str,
                               nis: Optional[str]) -> Optional[str]:
    now = time.time()
    cooldown = UNKNOWN_FACE_COOLDOWN_SECONDS if jenis_absen == "UNKNOWN_FACE" else ATTENDANCE_LOG_COOLDOWN_SECONDS
    with runtime.attendance_lock:
        if now - runtime.last_attendance_log.get(key, 0) < cooldown:
            return None
        runtime.last_attendance_log[key] = now

    try:
        student = student_db.get_student(nis) if student_db and nis else None
        photo_path = save_attendance_photo(frame, jenis_absen, nis or "UNKNOWN", student)
        if student_db:
            student_db.add_attendance_log(nis, jenis_absen, photo_path)
        return photo_path
    except Exception as exc:
        app_logger.exception(
            "attendance log save failed type=%s nis=%s",
            jenis_absen,
            nis or "UNKNOWN",
        )
        return None


def _attendance_log_due(key: str, jenis_absen: str) -> bool:
    now = time.time()
    cooldown = UNKNOWN_FACE_COOLDOWN_SECONDS if jenis_absen == "UNKNOWN_FACE" else ATTENDANCE_LOG_COOLDOWN_SECONDS
    with runtime.attendance_lock:
        if key in runtime.pending_attendance_logs:
            return False
        if now - runtime.last_attendance_log.get(key, 0) < cooldown:
            return False
        runtime.pending_attendance_logs.add(key)
        return True


def _voice_announcement_due(parent: Optional[Dict[str, Any]], settings: Dict[str, Any],
                            category: str) -> tuple[bool, Optional[str]]:
    if not parent or not settings.get("voice_announcement_enabled", True):
        return False, None

    nis = str(parent.get("nis", ""))
    cooldown = max(1, int(settings.get("voice_announcement_cooldown_seconds", 60)))
    cooldown_key = f"VOICE:{category}:{nis}"
    now = time.time()
    with runtime.voice_lock:
        if cooldown_key in runtime.pending_voice_announcements:
            return False, cooldown_key
        if now - runtime.last_voice_announcement.get(cooldown_key, 0) < cooldown:
            return False, cooldown_key
        runtime.pending_voice_announcements.add(cooldown_key)
        return True, cooldown_key


def _recognition_side_effect_worker(
    student_db: Optional[StudentDatabase],
    parent: Optional[Dict[str, Any]],
    settings: Dict[str, Any],
    key: str,
    frame: np.ndarray,
    face: Any,
    text: str,
    jenis_absen: str,
    nis: Optional[str],
    attendance_due: bool,
    voice_due: bool,
    voice_key: Optional[str],
    voice_category: str = "pickup",
) -> None:
    started = time.perf_counter()
    try:
        if voice_due and parent:
            try:
                queue_voice_announcement(parent, settings, category=voice_category)
            except Exception:
                app_logger.exception("voice announcement enqueue failed")
        if attendance_due:
            evidence = frame.copy()
            draw_box_and_text(evidence, face, text)
            record_attendance_evidence(student_db, key, evidence, jenis_absen, nis)
    finally:
        if attendance_due:
            with runtime.attendance_lock:
                runtime.pending_attendance_logs.discard(key)
        if voice_key:
            with runtime.voice_lock:
                runtime.pending_voice_announcements.discard(voice_key)

    elapsed = time.perf_counter() - started
    if elapsed >= RECOGNIZE_SLOW_FRAME_SECONDS:
        app_logger.info(
            "rec_side_slow ms=%.1f type=%s nis=%s attendance=%s voice=%s",
            elapsed * 1000,
            jenis_absen,
            nis or "UNKNOWN",
            attendance_due,
            voice_due,
        )


def enqueue_recognition_side_effects(
    student_db: Optional[StudentDatabase],
    parent: Optional[Dict[str, Any]],
    settings: Dict[str, Any],
    key: str,
    frame: np.ndarray,
    face: Any,
    text: str,
    jenis_absen: str,
    nis: Optional[str],
    voice_category: str = "pickup",
) -> bool:
    attendance_due = _attendance_log_due(key, jenis_absen)
    voice_due, voice_key = _voice_announcement_due(parent, settings, voice_category)
    if not attendance_due and not voice_due:
        return False

    threading.Thread(
        target=_recognition_side_effect_worker,
        args=(
            student_db,
            parent,
            settings.copy(),
            key,
            frame,
            face,
            text,
            jenis_absen,
            nis,
            attendance_due,
            voice_due,
            voice_key,
            voice_category,
        ),
        daemon=True,
    ).start()
    return True


def safe_voice_language(value: Any) -> str:
    lang = str(value or "id").strip().lower()
    return lang if re.fullmatch(r"[a-z]{2,3}(-[a-z]{2})?", lang) else "id"


VOICE_CATEGORY_KEYS = {
    # category -> settings key yang menyimpan template
    "pickup": "voice_announcement_template",
    "attendance": "voice_announcement_attendance_template",
}

VOICE_CATEGORY_FALLBACK = {
    "pickup": "Ananda {nama_anak} telah dijemput.",
    "attendance": "Ananda {nama_anak} telah hadir.",
}


def voice_text(
    parent: Dict[str, Any],
    settings: Dict[str, Any],
    category: str = "pickup",
) -> str:
    """
    Susun teks yang akan dibacakan TTS.

    `category`:
      - "pickup"     -> pakai voice_announcement_template (anak dijemput)
      - "attendance" -> pakai voice_announcement_attendance_template (anak datang)
    """
    settings_key = VOICE_CATEGORY_KEYS.get(category, "voice_announcement_template")
    fallback = VOICE_CATEGORY_FALLBACK.get(category, VOICE_CATEGORY_FALLBACK["pickup"])
    template = str(settings.get(settings_key) or fallback)
    values = {
        "nis": str(parent.get("nis", "")),
        "nama_anak": str(parent.get("nama_anak", "")),
        "nama_ortu": str(parent.get("nama_ortu", "")),
        "kelas": str(parent.get("kelas", "")),
    }
    try:
        text = template.format(**values)
    except Exception:
        text = fallback.format(**values) if "{nama_anak}" in fallback else fallback
    return " ".join(text.split())


def tts_cache_target(text: str, language: str) -> tuple[Path, str]:
    """Return deterministic cache path + URL for one voice phrase."""
    language = safe_voice_language(language)
    digest = hashlib.sha256(f"{language}:{text}".encode("utf-8")).hexdigest()[:24]
    filename = f"{language}_{digest}.mp3"
    return TTS_CACHE_DIR / filename, f"/api/voice/audio/{filename}"


def tts_audio_url(text: str, language: str) -> Optional[str]:
    if not text:
        return None
    if gTTS is None:
        app_logger.error("voice generation unavailable: gTTS is not installed")
        return None

    language = safe_voice_language(language)
    path, audio_url = tts_cache_target(text, language)
    if not path.exists():
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        try:
            gTTS(text=text, lang=language).save(str(tmp_path))
            os.replace(tmp_path, path)
        except Exception as exc:
            if tmp_path.exists():
                tmp_path.unlink()
            app_logger.exception("voice TTS generation failed: %s", exc)
            return None
    return audio_url


def pregenerate_voice_cache_for_student(
    student: Dict[str, Any],
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """
    Pre-generate MP3 TTS untuk satu siswa di kedua kategori (pickup &
    attendance) supaya cache lokal sudah penuh sebelum gate dipakai
    offline. Aman dipanggil setelah enroll atau lewat endpoint batch.

    Pastikan TTS_CACHE_DIR ada. Setiap kegagalan (mis. internet putus)
    di-log tapi tidak melempar exception agar enroll tetap berhasil.
    """
    if settings is None:
        settings = load_settings()
    if not settings.get("voice_announcement_enabled", True) or gTTS is None:
        return {"pickup": None, "attendance": None}

    language = settings.get("voice_announcement_language", "id")
    parent_like = {
        "nis": str(student.get("nis", "")),
        "nama_anak": str(student.get("nama") or student.get("nama_anak") or ""),
        "nama_ortu": str(student.get("nama_ortu", "")),
        "kelas": str(student.get("kelas", "")),
    }

    results: Dict[str, Optional[str]] = {}
    for category in VOICE_CATEGORY_KEYS:
        try:
            text = voice_text(parent_like, settings, category=category)
            results[category] = tts_audio_url(text, language)
        except Exception as exc:
            app_logger.exception(
                "voice pre-generation failed category=%s nis=%s",
                category,
                parent_like["nis"],
            )
            results[category] = None
    return results


def queue_voice_announcement(
    parent: Dict[str, Any],
    settings: Dict[str, Any],
    category: str = "pickup",
) -> Optional[Dict[str, Any]]:
    """
    Antrikan pengumuman suara untuk `parent` (atau siswa di kasus attendance).

    `category`:
      - "pickup"     -> face pickup atau QR penjemputan
      - "attendance" -> QR kehadiran siswa pagi

    Cooldown disimpan per (NIS, category) supaya scan kehadiran dan
    scan penjemputan untuk siswa yang sama tidak saling memblokir.
    """
    if not settings.get("voice_announcement_enabled", True):
        return None

    nis = str(parent.get("nis", ""))
    now = time.time()
    cooldown = max(1, int(settings.get("voice_announcement_cooldown_seconds", 60)))
    cooldown_key = f"VOICE:{category}:{nis}"
    with runtime.voice_lock:
        if now - runtime.last_voice_announcement.get(cooldown_key, 0) < cooldown:
            return None
        runtime.last_voice_announcement[cooldown_key] = now

    text = voice_text(parent, settings, category=category)
    audio_url = tts_audio_url(text, settings.get("voice_announcement_language", "id"))
    if not audio_url:
        return None

    with runtime.voice_lock:
        runtime.voice_event_id += 1
        event = {
            "id": runtime.voice_event_id,
            "nis": nis,
            "nama_anak": parent.get("nama_anak"),
            "nama_ortu": parent.get("nama_ortu"),
            "kelas": parent.get("kelas"),
            "category": category,
            "text": text,
            "audio_url": audio_url,
            "created_at": int(now),
        }
        runtime.voice_events.append(event)
        return event


class StreamStats:
    def __init__(self):
        self.started_at = time.time()
        self.frame_timestamps = deque(maxlen=120)
        self.frame_times = deque(maxlen=60)
        self.inference_times = deque(maxlen=30)
        self.process = psutil.Process()
        self.cpu_count = psutil.cpu_count(logical=True) or 1
        self.process.cpu_percent(interval=None)

    def add_frame(self, duration: float):
        self.frame_timestamps.append(time.perf_counter())
        self.frame_times.append(duration)

    def add_inference(self, duration: float):
        self.inference_times.append(duration)

    def overlay_text(self) -> str:
        fps = 0.0
        if len(self.frame_timestamps) >= 2:
            elapsed = self.frame_timestamps[-1] - self.frame_timestamps[0]
            if elapsed > 0:
                fps = (len(self.frame_timestamps) - 1) / elapsed
        frame_ms = (sum(self.frame_times) / len(self.frame_times) * 1000) if self.frame_times else 0.0
        inference_ms = (
            sum(self.inference_times) / len(self.inference_times) * 1000
        ) if self.inference_times else 0.0
        raw_cpu = self.process.cpu_percent(interval=None)
        app_cpu = min(100.0, raw_cpu / self.cpu_count)
        ram_mb = self.process.memory_info().rss / 1024 / 1024
        return (
            f"FPS: {fps:.1f} | Frame: {frame_ms:.1f}ms | "
            f"Inference: {inference_ms:.1f}ms | App CPU: {app_cpu:.1f}% | "
            f"App RAM: {ram_mb:.0f}MB"
        )


def draw_performance_overlay(frame: np.ndarray, text: str) -> None:
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, h - 36), (w, h), (0, 0, 0), -1)
    cv2.putText(
        frame,
        text,
        (8, h - 13),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (0, 255, 0),
        1,
        cv2.LINE_AA,
    )


class RuntimeState:
    def __init__(self):
        self._lock = threading.Lock()
        self._face_app = None
        self._face_app_key = None
        self.attendance_lock = threading.Lock()
        self.voice_lock = threading.Lock()
        self.enroll_sessions: Dict[str, Dict[str, Any]] = {}
        self.last_attendance_log: Dict[str, float] = {}
        self.last_voice_announcement: Dict[str, float] = {}
        self.pending_attendance_logs = set()
        self.pending_voice_announcements = set()
        self.voice_events = deque(maxlen=20)
        self.voice_event_id = 0

    def get_face_app(self, settings: Dict[str, Any]):
        key = ("buffalo_sc", int(settings["det_size"]), "cpu")
        with self._lock:
            if self._face_app is None or self._face_app_key != key:
                self._face_app = build_face_app(
                    model_name=key[0],
                    det_size=key[1],
                    device=key[2],
                )
                self._face_app_key = key
            return self._face_app


runtime = RuntimeState()


class CameraHub:
    """Single shared camera capture for all web MJPEG streams.

    Browsers do not always close the previous image stream immediately when the
    src changes. Sharing one VideoCapture prevents Preview and Recognize from
    fighting over the same camera device.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._cap = None
        self._thread = None
        self._running = False
        self._refs = 0
        self._key = None
        self._latest_frame = None
        self._last_error = None

    def _settings_key(self, settings: Dict[str, Any]):
        return (
            int(settings["cam_index"]),
            int(settings["width"]),
            int(settings["height"]),
        )

    def acquire(self, settings: Dict[str, Any]):
        key = self._settings_key(settings)
        with self._lock:
            if self._running and self._key != key:
                self._stop_locked()
            if not self._running:
                self._start_locked(settings, key)
            self._refs += 1

    def release(self):
        with self._lock:
            self._refs = max(0, self._refs - 1)
            if self._refs == 0:
                self._stop_locked()

    def force_stop(self):
        """Force release capture + reset refs ke 0.
        Dipakai endpoint /api/camera/release saat user balik ke menu home —
        Chromium kadang nahan TCP MJPEG socket sehingga generator stream
        tidak exit secara alami; force_stop memastikan kamera benar2 mati."""
        with self._lock:
            self._refs = 0
            self._stop_locked()

    def _start_locked(self, settings: Dict[str, Any], key):
        self._cap = open_camera(key[0], key[1], key[2])
        self._key = key
        self._running = True
        self._last_error = None
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _stop_locked(self):
        self._running = False
        thread = self._thread
        cap = self._cap
        self._thread = None
        self._cap = None
        self._key = None

        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        if cap:
            cap.release()

    def _capture_loop(self):
        while self._running:
            try:
                ok, frame = self._cap.read() if self._cap else (False, None)
                if ok:
                    with self._frame_lock:
                        self._latest_frame = frame
                    self._last_error = None
                else:
                    self._last_error = "Gagal membaca frame dari kamera."
                    time.sleep(0.05)
            except Exception as exc:
                self._last_error = str(exc)
                time.sleep(0.1)

    def get_frame(self, mirror: bool = False, timeout: float = 2.0) -> np.ndarray:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._frame_lock:
                frame = None if self._latest_frame is None else self._latest_frame.copy()
            if frame is not None:
                if mirror:
                    frame = cv2.flip(frame, 1)
                return frame
            time.sleep(0.02)
        raise RuntimeError(self._last_error or "Kamera belum menghasilkan frame.")


camera_hub = CameraHub()


class RecognizeBroadcaster:
    """Single inference worker, multi-subscriber recognize stream.

    Sebelumnya tiap request /video/recognize bikin loopnya sendiri
    (kamera + inference + drawing), di-serialize via recognize_lock
    supaya cuma 1 jalan barengan. Akibatnya client kedua langsung dapat
    pesan kuning "stream already active".

    Sekarang: hanya 1 worker thread di belakang layar (saat ada >=1 subscriber)
    yang menjalankan inference + drawing. Banyak HTTP client bisa nonton
    barengan karena mereka cuma mengambil latest annotated frame dari shared
    buffer, bukan jalankan inference sendiri. CPU tetap aman.

    Catatan: embeddings & student_db hanya di-load saat worker start.
    Jadi setiap kali subscriber count kembali ke 0 lalu naik lagi, worker
    di-restart dan data fresh. Selama worker masih jalan, enrollment baru
    belum terlihat — restart service / refresh stream untuk apply.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._subscribers = 0
        self._latest_bytes: Optional[bytes] = None
        self._seq = 0
        self._running = False

    def subscribe(self, settings: Dict[str, Any]) -> None:
        with self._lock:
            self._subscribers += 1
            start_worker = not self._running
            if start_worker:
                self._running = True
                self._seq = 0
                self._latest_bytes = None
        if start_worker:
            threading.Thread(target=self._loop, args=(settings,), daemon=True).start()

    def unsubscribe(self) -> None:
        with self._cond:
            self._subscribers = max(0, self._subscribers - 1)
            if self._subscribers == 0:
                self._running = False
                self._cond.notify_all()

    def force_stop(self) -> None:
        """Force reset subscriber count + stop worker. Mirror dari
        CameraHub.force_stop — dipakai saat user back ke home menu."""
        with self._cond:
            self._subscribers = 0
            self._running = False
            self._cond.notify_all()

    def wait_frame(self, last_seq: int, timeout: float = 3.0):
        """Block sampai ada frame baru (seq > last_seq) atau worker stop.
        Return (frame_bytes, new_seq) atau None kalau worker stop / timeout."""
        with self._cond:
            self._cond.wait_for(
                lambda: self._seq > last_seq or not self._running,
                timeout=timeout,
            )
            if self._seq > last_seq and self._latest_bytes is not None:
                return self._latest_bytes, self._seq
            return None

    def _publish(self, frame_bytes: bytes) -> None:
        with self._cond:
            self._latest_bytes = frame_bytes
            self._seq += 1
            self._cond.notify_all()

    def _loop(self, settings: Dict[str, Any]) -> None:
        face_db = FaceDB(str(FACE_DB_DIR))
        embs = face_db.load()
        stats = StreamStats()
        frame_count = 0
        last_result = []
        last_status = None
        last_inference = 0.0
        parent_cache: "OrderedDict[int, Optional[Dict[str, Any]]]" = OrderedDict()
        slow_recognize_log_at = 0.0
        db_error = None
        try:
            student_db = StudentDatabase()
        except Exception as exc:
            student_db = None
            db_error = str(exc)

        camera_hub.acquire(settings)
        led = get_led()
        led.set_idle()
        try:
            face_app = runtime.get_face_app(settings)
            while self._running:
                frame_start = time.perf_counter()
                try:
                    frame = camera_hub.get_frame(mirror=bool(settings.get("mirror_camera")))
                except Exception as exc:
                    # Kamera gagal — publish frame error supaya client lihat status
                    err_frame = np.zeros((240, 320, 3), dtype=np.uint8)
                    cv2.putText(err_frame, f"Camera error: {str(exc)[:30]}", (10, 120),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                    self._publish(mjpeg_part(err_frame))
                    time.sleep(0.5)
                    continue

                frame_count += 1
                disp = frame.copy()
                recognize_debug = None

                if db_error:
                    cv2.putText(disp, f"MySQL error: {db_error[:55]}", (15, 35),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                elif len(embs) == 0:
                    cv2.putText(disp, "Database wajah kosong", (15, 35),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                elif frame_count % 3 == 0 and (time.time() - last_inference) >= 0.18:
                    last_inference = time.time()
                    infer_frame, infer_scale = _prepare_inference_frame(frame)
                    inference_start = time.perf_counter()
                    faces = face_app.get(infer_frame)
                    inference_elapsed = time.perf_counter() - inference_start
                    stats.add_inference(inference_elapsed)
                    last_result = []
                    parent_lookup_ms = 0.0
                    queued_side_effects = 0
                    for face in faces[:3]:
                        if infer_scale != 1.0:
                            face.bbox = (face.bbox.astype(np.float32) * infer_scale)
                        if float(face.det_score) < float(settings["min_det_score"]):
                            continue

                        emb = l2_normalize(face.normed_embedding.astype(np.float32))
                        sims = embs @ emb
                        best_idx = int(np.argmax(sims))
                        best_sim = float(sims[best_idx])

                        if best_sim >= float(settings["threshold"]) and student_db:
                            if best_idx not in parent_cache:
                                parent_lookup_start = time.perf_counter()
                                parent_cache[best_idx] = student_db.get_parent_by_index(best_idx)
                                parent_lookup_ms += (time.perf_counter() - parent_lookup_start) * 1000
                                if len(parent_cache) > PARENT_CACHE_MAX_SIZE:
                                    parent_cache.popitem(last=False)
                            else:
                                parent_cache.move_to_end(best_idx)
                            parent = parent_cache[best_idx]
                            if parent:
                                nis = str(parent["nis"])
                                text = (
                                    f"Ortu: {parent['nama_ortu']}\n"
                                    f"Anak: {parent['nama_anak']} ({parent['kelas']})\n"
                                    f"NIS: {nis}"
                                )
                                key = f"PENJEMPUTAN_FACE:{nis}"
                                if enqueue_recognition_side_effects(
                                    student_db,
                                    parent,
                                    settings,
                                    key,
                                    frame,
                                    face,
                                    text,
                                    "PENJEMPUTAN_FACE",
                                    nis,
                                ):
                                    queued_side_effects += 1
                                last_status = {
                                    "lines": [
                                        f"Ortu: {parent['nama_ortu']}",
                                        f"Anak: {parent['nama_anak']} ({parent['kelas']})",
                                        f"NIS: {nis}",
                                    ],
                                    "ok": True,
                                    "expires_at": time.time() + 3.0,
                                }
                                led.set_recognized()
                            else:
                                text = f"Index:{best_idx} belum ada di MySQL | sim={best_sim:.2f}"
                                led.set_unknown()
                        else:
                            text = f"Unknown | sim={best_sim:.2f}"
                            if enqueue_recognition_side_effects(
                                student_db,
                                None,
                                settings,
                                "UNKNOWN_FACE",
                                frame,
                                face,
                                text,
                                "UNKNOWN_FACE",
                                None,
                            ):
                                queued_side_effects += 1
                            led.set_unknown()
                        last_result.append((face, text))
                    recognize_debug = {
                        "faces": len(faces),
                        "inference_ms": inference_elapsed * 1000,
                        "parent_lookup_ms": parent_lookup_ms,
                        "queued_side_effects": queued_side_effects,
                        "infer_width": infer_frame.shape[1],
                    }

                for face, text in last_result:
                    draw_box_and_text(disp, face, text)

                if last_status and time.time() <= last_status.get("expires_at", 0):
                    draw_status_text(disp, last_status.get("lines", []), ok=bool(last_status.get("ok")))
                    if last_status.get("ok"):
                        draw_success_badge(disp)

                frame_elapsed = time.perf_counter() - frame_start
                stats.add_frame(frame_elapsed)
                if frame_elapsed >= RECOGNIZE_SLOW_FRAME_SECONDS and time.time() >= slow_recognize_log_at:
                    slow_recognize_log_at = time.time() + 2.0
                    debug = recognize_debug or {}
                    app_logger.info(
                        "rec_frame_slow ms=%.1f faces=%s infer_ms=%.1f parent_ms=%.1f queued=%s infer_w=%s",
                        frame_elapsed * 1000,
                        debug.get("faces", 0),
                        float(debug.get("inference_ms", 0.0)),
                        float(debug.get("parent_lookup_ms", 0.0)),
                        debug.get("queued_side_effects", 0),
                        debug.get("infer_width", 0),
                    )
                # Face recognition display stays clean; QR stream still honors show_performance.

                self._publish(mjpeg_part(disp))
                time.sleep(0.04)
        finally:
            led.off()
            camera_hub.release()
            with self._cond:
                self._cond.notify_all()


recognize_broadcaster = RecognizeBroadcaster()


class StudentIn(BaseModel):
    nis: str = Field(..., min_length=1, max_length=32)
    nama: str = Field(..., min_length=1, max_length=255)
    kelas: str = Field(..., min_length=1, max_length=64)


class StudentUpdate(BaseModel):
    nama: str = Field(..., min_length=1, max_length=255)
    kelas: str = Field(..., min_length=1, max_length=64)


class ParentUpdate(BaseModel):
    nis: str = Field(..., min_length=1, max_length=32)
    nama_ortu: str = Field(..., min_length=1, max_length=255)


class SettingsPatch(BaseModel):
    cam_index: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    det_size: Optional[int] = None
    min_det_score: Optional[float] = None
    samples: Optional[int] = None
    auto_capture_enroll: Optional[bool] = None
    auto_capture_interval_ms: Optional[int] = None
    voice_announcement_enabled: Optional[bool] = None
    voice_announcement_language: Optional[str] = None
    voice_announcement_cooldown_seconds: Optional[int] = None
    voice_announcement_template: Optional[str] = None
    threshold: Optional[float] = None
    show_performance: Optional[bool] = None
    mirror_camera: Optional[bool] = None


class EnrollStart(BaseModel):
    nis: str = Field(..., min_length=1)
    parent_name: str = Field(..., min_length=1)
    samples: Optional[int] = Field(None, ge=1, le=30)


class FrameCapture(BaseModel):
    session_id: str
    image_data: str


class VoiceTestIn(BaseModel):
    text: Optional[str] = None


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class CancelLogIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=255)


class ClientErrorIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    source: Optional[str] = Field(default=None, max_length=500)
    line: Optional[int] = Field(default=None, ge=0)
    column: Optional[int] = Field(default=None, ge=0)
    stack: Optional[str] = Field(default=None, max_length=8000)
    page: Optional[str] = Field(default=None, max_length=500)
    user_agent: Optional[str] = Field(default=None, max_length=500)


app = FastAPI(title="SafePick Web UI")
app.mount("/static", StaticFiles(directory=str(app_path("frontend", "static"))), name="static")
templates = Jinja2Templates(directory=str(app_path("frontend", "templates")))


def _safe_request_id(value: Optional[str]) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value):
        return value
    return uuid.uuid4().hex[:12]


def _safe_log_text(value: Optional[str], limit: int) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ")[:limit]
    return re.sub(
        r"(?i)(password|passwd|token|secret|cookie|authorization|psk)"
        r"\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        text,
    )


def _allow_client_error(client_host: str) -> bool:
    now = time.monotonic()
    with _client_error_lock:
        timestamps = _client_error_times.setdefault(client_host, deque())
        while timestamps and now - timestamps[0] > 60:
            timestamps.popleft()
        if len(timestamps) >= 20:
            return False
        timestamps.append(now)
        return True


@app.middleware("http")
async def diagnostic_request_middleware(request: Request, call_next):
    started = time.perf_counter()
    request_id = _safe_request_id(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    client_host = request.client.host if request.client else "-"

    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id

    message = (
        "request id=%s client=%s method=%s path=%s status=%s duration_ms=%.1f"
    )
    args = (
        request_id,
        client_host,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    noisy_path = (
        request.url.path.startswith("/static/")
        or request.url.path.startswith("/video/")
        or request.url.path == "/api/voice/events"
    )
    if response.status_code >= 500:
        app_logger.error(message, *args)
    elif response.status_code >= 400:
        app_logger.warning(message, *args)
    elif noisy_path:
        app_logger.debug(message, *args)
    else:
        app_logger.info(message, *args)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
    app_logger.error(
        "unhandled request error id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


@app.on_event("startup")
def _log_startup() -> None:
    app_logger.info(
        "application started root=%s python=%s pid=%s log_level=%s",
        APP_ROOT,
        sys.version.split()[0],
        os.getpid(),
        _env("SAFEPICK_LOG_LEVEL", "FACEGATE_LOG_LEVEL", "INFO").upper(),
    )


@app.on_event("shutdown")
def _shutdown_hardware() -> None:
    """Pastikan semua LED mati saat aplikasi stop."""
    try:
        shutdown_led()
    except Exception as exc:
        app_logger.exception("shutdown LED cleanup failed: %s", exc)
    app_logger.info("application stopped uptime_seconds=%.1f", time.time() - APP_STARTED_AT)


@app.get("/")
def index():
    return RedirectResponse(url="/display")


@app.get("/display", response_class=HTMLResponse)
def display(request: Request):
    return templates.TemplateResponse(request, "display.html")


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    if not read_session(request):
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request, "admin.html")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if read_session(request):
        return RedirectResponse(url="/admin")
    return templates.TemplateResponse(request, "login.html")


@app.post("/api/login")
def login(payload: LoginIn, response: Response):
    try:
        ok = AdminAccountStore().verify(payload.username, payload.password)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MySQL account error: {exc}") from exc

    if not ok:
        raise HTTPException(status_code=401, detail="Username atau password salah.")

    token = sign_session(payload.username.strip(), int(time.time()))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/session")
def session(request: Request):
    username = require_admin(request)
    return {"ok": True, "username": username}


@app.post("/api/debug/client-error", status_code=204)
def client_error(payload: ClientErrorIn, request: Request):
    """Receive sanitized browser errors; rate-limited to prevent log flooding."""
    client_host = request.client.host if request.client else "unknown"
    if not _allow_client_error(client_host):
        return Response(status_code=204)

    app_logger.error(
        "browser error client=%s page=%s source=%s line=%s column=%s "
        "message=%s stack=%s user_agent=%s",
        client_host,
        _safe_log_text(payload.page, 500),
        _safe_log_text(payload.source, 500),
        payload.line,
        payload.column,
        _safe_log_text(payload.message, 2000),
        _safe_log_text(payload.stack, 8000),
        _safe_log_text(payload.user_agent, 500),
    )
    return Response(status_code=204)


@app.get("/api/debug/status")
def debug_status(request: Request):
    """Small localhost-only snapshot for remote troubleshooting."""
    _require_localhost(request)
    process = psutil.Process()
    disk = psutil.disk_usage(str(APP_ROOT))
    memory = psutil.virtual_memory()
    return {
        "ok": True,
        "request_id": getattr(request.state, "request_id", None),
        "pid": os.getpid(),
        "uptime_seconds": round(time.time() - APP_STARTED_AT, 1),
        "python": sys.version.split()[0],
        "memory": {
            "process_rss_mb": round(process.memory_info().rss / (1024 * 1024), 1),
            "system_percent": memory.percent,
            "available_mb": round(memory.available / (1024 * 1024), 1),
        },
        "disk": {
            "percent": disk.percent,
            "free_mb": round(disk.free / (1024 * 1024), 1),
        },
        "threads": threading.active_count(),
        "log_file": str(APP_ROOT / "logs" / "app.log"),
    }


def _read_cpu_temperature_c() -> Optional[float]:
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False)
        for name in ("cpu_thermal", "coretemp", "soc_thermal"):
            entries = temps.get(name) or []
            for entry in entries:
                if entry.current is not None:
                    return round(float(entry.current), 1)
        for entries in temps.values():
            for entry in entries:
                if entry.current is not None:
                    return round(float(entry.current), 1)
    except Exception:
        pass

    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        if thermal_path.exists():
            return round(float(thermal_path.read_text().strip()) / 1000.0, 1)
    except Exception:
        return None
    return None


def performance_snapshot() -> Dict[str, Any]:
    process = psutil.Process()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(APP_ROOT))
    cpu_freq = psutil.cpu_freq()
    try:
        load_avg = os.getloadavg()
    except (AttributeError, OSError):
        load_avg = None

    return {
        "cpu_temp_c": _read_cpu_temperature_c(),
        "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
        "cpu_count": psutil.cpu_count(logical=True) or 1,
        "cpu_freq_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
        "load_avg": [round(value, 2) for value in load_avg] if load_avg else None,
        "memory_percent": memory.percent,
        "memory_used_mb": round(memory.used / (1024 * 1024), 1),
        "memory_total_mb": round(memory.total / (1024 * 1024), 1),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / (1024 * 1024 * 1024), 2),
        "disk_total_gb": round(disk.total / (1024 * 1024 * 1024), 2),
        "process_rss_mb": round(process.memory_info().rss / (1024 * 1024), 1),
        "threads": threading.active_count(),
        "uptime_seconds": round(time.time() - APP_STARTED_AT, 1),
    }


@app.get("/api/performance")
def performance():
    return performance_snapshot()


@app.get("/api/status")
def status():
    db_result = db_call(lambda db: db.get_summary())
    db_result["name"] = _env("SAFEPICK_DB_NAME", "FACEGATE_DB_NAME", "safepick")
    return {
        "settings": load_settings(),
        "database": db_result,
        "embeddings": embedding_info(),
        "qr_count": qr_count(),
    }


@app.get("/api/students")
def students(request: Request):
    require_admin(request)
    result = db_call(lambda db: db.list_all_students())
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    return result["data"]


@app.post("/api/students")
def add_student(student: StudentIn, request: Request):
    require_admin(request)
    result = db_call(lambda db: db.add_student(student.nis.strip(), student.nama.strip(), student.kelas.strip()))
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    if not result["data"]:
        raise HTTPException(status_code=409, detail="NIS sudah ada.")
    return {"ok": True}


@app.put("/api/students/{nis}")
def update_student(nis: str, student: StudentUpdate, request: Request):
    require_admin(request)
    result = db_call(lambda db: db.update_student(nis.strip(), student.nama.strip(), student.kelas.strip()))
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    if not result["data"]:
        raise HTTPException(status_code=404, detail="NIS tidak ditemukan.")
    return {"ok": True}


@app.delete("/api/students/{nis}")
def delete_student(nis: str, request: Request):
    require_admin(request)
    result = db_call(lambda db: db.delete_student(nis.strip()))
    if not result["ok"]:
        error = result["error"]
        if "foreign key" in error.lower():
            raise HTTPException(
                status_code=409,
                detail="Siswa masih punya parent/enrollment. Delete parent dulu sebelum hapus siswa.",
            )
        raise HTTPException(status_code=503, detail=error)
    if not result["data"]:
        raise HTTPException(status_code=404, detail="NIS tidak ditemukan.")
    return {"ok": True}


@app.get("/api/parents")
def parents(request: Request):
    require_admin(request)
    result = db_call(lambda db: db.list_all_parents())
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    return result["data"]


@app.put("/api/parents/{parent_id}")
def update_parent(parent_id: int, parent: ParentUpdate, request: Request):
    require_admin(request)
    result = db_call(lambda db: db.update_parent(parent_id, parent.nis.strip(), parent.nama_ortu.strip()))
    if not result["ok"]:
        error = result["error"]
        if "foreign key" in error.lower():
            raise HTTPException(status_code=409, detail="NIS siswa belum ada di database.")
        raise HTTPException(status_code=503, detail=error)
    if not result["data"]:
        raise HTTPException(status_code=404, detail="Parent tidak ditemukan.")
    return {"ok": True}


@app.delete("/api/parents/{parent_id}")
def delete_parent(parent_id: int, request: Request):
    require_admin(request)
    result = db_call(lambda db: db.delete_parent(parent_id))
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    embedding_index = result["data"]
    if embedding_index is None:
        raise HTTPException(status_code=404, detail="Parent tidak ditemukan.")

    FaceDB(str(FACE_DB_DIR)).tombstone(embedding_index)
    vacuum_result = vacuum_embeddings_file()
    return {"ok": True, "embedding_index": embedding_index, "vacuum": vacuum_result}


def vacuum_embeddings_file():
    """
    Rebuild embeddings.npy untuk membuang baris ghost (tombstoned atau orphan)
    dan reindex parents.embedding_index ke posisi baru. Dijalankan setelah
    reset parent atau manual saat file mulai membengkak.

    Sejak embedding terenkripsi, semua I/O ke `embeddings.npy` lewat
    FaceDB.load_raw()/save_raw() supaya blob Fernet tidak rusak. Urutan
    operasi tetap (DB dulu, file belakangan) supaya kalau crash di tengah:
      - DB UPDATE gagal -> file utama utuh, no-op.
      - DB sudah commit tapi save_raw gagal -> DB punya index baru tapi
        file masih lama. Admin perlu rerun vacuum untuk recover.
    """
    fdb = FaceDB(str(FACE_DB_DIR))
    if not Path(fdb.emb_path).exists():
        return {"ok": True, "removed": 0, "kept": 0,
                "message": "Tidak ada embeddings.npy."}

    parents_result = db_call(lambda db: db.list_all_parents())
    if not parents_result["ok"]:
        raise HTTPException(status_code=503, detail=parents_result["error"])
    parents = parents_result["data"]

    embs = fdb.load_raw()
    n_old = int(embs.shape[0]) if embs.ndim >= 2 else 0

    # Index yang masih dirujuk parent aktif.
    active_indices = sorted({
        p["embedding_index"] for p in parents
        if 0 <= p["embedding_index"] < n_old
    })

    if not active_indices:
        # Tidak ada parent aktif -> buang file embeddings sekalian.
        fdb.save_raw(np.zeros((0, embs.shape[1] if embs.ndim == 2 else 512),
                              dtype=np.float32))
        return {"ok": True, "removed": n_old, "kept": 0}

    # Kalau sudah rapi (kontigu mulai 0, panjang sama), tidak perlu rewrite.
    already_clean = (len(active_indices) == n_old
                     and active_indices == list(range(n_old)))
    if already_clean:
        return {"ok": True, "removed": 0, "kept": n_old,
                "message": "Sudah bersih."}

    # Rebuild array hanya dengan baris aktif, susun ulang.
    new_embs = embs[active_indices]
    remap = {old: new for new, old in enumerate(active_indices)}
    assignments = [
        (p["parent_id"], remap[p["embedding_index"]])
        for p in parents
        if p["embedding_index"] in remap
    ]

    # 1) Bulk-reassign embedding_index di MySQL (atomic transaction).
    try:
        db_result = db_call(
            lambda db: db.bulk_reassign_embedding_indices(assignments))
        if not db_result["ok"]:
            raise RuntimeError(db_result["error"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Gagal update DB: {exc}")

    # 2) Tulis ulang file embeddings (sudah dienkripsi otomatis lewat FaceDB).
    try:
        fdb.save_raw(new_embs)
    except Exception as exc:
        # DB sudah commit di langkah 1; file masih lama. Beritahu admin.
        raise HTTPException(
            status_code=500,
            detail=(
                f"Gagal tulis embeddings.npy terenkripsi: {exc}. "
                "DB sudah ter-update; jalankan vacuum lagi untuk recover."
            ),
        )

    return {
        "ok": True,
        "removed": n_old - len(active_indices),
        "kept": len(active_indices),
    }


@app.post("/api/maintenance/vacuum")
def vacuum_embeddings(request: Request):
    require_admin(request)
    return vacuum_embeddings_file()


@app.get("/api/network/info")
def network_info():
    """
    Kembalikan info network yang dibutuhkan UI display untuk
    menampilkan popup URL admin. Tidak butuh auth karena hanya
    menampilkan IP LAN (informasi network publik di LAN itu sendiri).
    """
    port = _env("SAFEPICK_WEB_PORT", "FACEGATE_WEB_PORT", "8000")
    lan_ip = primary_lan_ip()
    return {
        "lan_ip": lan_ip,
        "port": int(port),
        "admin_url": f"http://{lan_ip}:{port}/admin",
        "display_url": f"http://{lan_ip}:{port}/display",
    }


def _require_localhost(request: Request) -> None:
    """Guard untuk endpoint yang hanya boleh dipanggil dari Pi sendiri."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=403,
            detail="Endpoint ini hanya bisa dipanggil dari Pi sendiri (localhost).",
        )


class WifiConnectIn(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=64)
    password: Optional[str] = Field(default=None, max_length=128)


def _wifi_helper():
    """Import dengan handling kalau platform tidak punya nmcli."""
    from backend.utils import wifi as wifi_helper
    return wifi_helper


@app.get("/api/wifi/status")
def wifi_status(request: Request):
    """Status koneksi WiFi saat ini. Localhost-only."""
    _require_localhost(request)
    wifi_helper = _wifi_helper()
    try:
        return wifi_helper.status()
    except wifi_helper.NmcliNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/wifi/scan")
def wifi_scan(request: Request):
    """List WiFi networks nearby. Localhost-only. Rescan terjadi otomatis (~3-5s)."""
    _require_localhost(request)
    wifi_helper = _wifi_helper()
    try:
        return {"networks": wifi_helper.scan()}
    except wifi_helper.NmcliNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/wifi/connect")
def wifi_connect(payload: WifiConnectIn, request: Request):
    """Connect ke SSID. Localhost-only. Body: {ssid, password?}."""
    _require_localhost(request)
    wifi_helper = _wifi_helper()
    try:
        return wifi_helper.connect(payload.ssid, payload.password)
    except wifi_helper.NmcliNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── Bluetooth endpoints (localhost-only) ────────────────────────────────
class BluetoothMacIn(BaseModel):
    mac: str = Field(..., min_length=17, max_length=17)


def _bt_helper():
    """Import bluetooth helper lazily — di Windows dev, bluetoothctl gak ada."""
    from backend.utils import bluetooth as bt_helper
    return bt_helper


@app.get("/api/bluetooth/status")
def bluetooth_status(request: Request):
    """Status adapter + device yang sedang connect. Localhost-only."""
    _require_localhost(request)
    bt_helper = _bt_helper()
    try:
        return bt_helper.status()
    except bt_helper.BluetoothctlNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/bluetooth/scan")
def bluetooth_scan(request: Request):
    """Scan ~8 detik untuk device nearby. Localhost-only."""
    _require_localhost(request)
    bt_helper = _bt_helper()
    try:
        return {"devices": bt_helper.scan()}
    except bt_helper.BluetoothctlNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/bluetooth/connect")
def bluetooth_connect(payload: BluetoothMacIn, request: Request):
    """Pair (kalau belum) + trust + connect. Localhost-only. Body: {mac}."""
    _require_localhost(request)
    bt_helper = _bt_helper()
    try:
        return bt_helper.pair_and_connect(payload.mac)
    except bt_helper.BluetoothctlNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/bluetooth/disconnect")
def bluetooth_disconnect(payload: BluetoothMacIn, request: Request):
    """Disconnect tanpa unpair. Localhost-only."""
    _require_localhost(request)
    bt_helper = _bt_helper()
    try:
        return bt_helper.disconnect(payload.mac)
    except bt_helper.BluetoothctlNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/bluetooth/unpair")
def bluetooth_unpair(payload: BluetoothMacIn, request: Request):
    """Remove pairing (lupakan device). Localhost-only."""
    _require_localhost(request)
    bt_helper = _bt_helper()
    try:
        return bt_helper.unpair(payload.mac)
    except bt_helper.BluetoothctlNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── Volume endpoints (localhost-only) ───────────────────────────────────
class VolumeSetIn(BaseModel):
    percent: int = Field(..., ge=0, le=100)


class VolumeMuteIn(BaseModel):
    muted: bool


def _volume_helper():
    """Import volume helper lazily — di Windows dev, amixer gak ada."""
    from backend.utils import volume as vol_helper
    return vol_helper


@app.get("/api/volume/status")
def volume_status(request: Request):
    """Volume Master saat ini: {control, percent, muted}. Localhost-only."""
    _require_localhost(request)
    vol_helper = _volume_helper()
    try:
        return vol_helper.status()
    except vol_helper.AmixerNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/volume/set")
def volume_set(payload: VolumeSetIn, request: Request):
    """Set volume Master 0-100%. Localhost-only. Body: {percent}."""
    _require_localhost(request)
    vol_helper = _volume_helper()
    try:
        return vol_helper.set_volume(payload.percent)
    except vol_helper.AmixerNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/volume/mute")
def volume_mute(payload: VolumeMuteIn, request: Request):
    """Mute / unmute Master. Localhost-only. Body: {muted}."""
    _require_localhost(request)
    vol_helper = _volume_helper()
    try:
        return vol_helper.set_muted(payload.muted)
    except vol_helper.AmixerNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/camera/release")
def release_camera(request: Request):
    """Force release kamera + stop semua stream worker.

    Dipanggil frontend saat user balik ke menu Home — Chromium kadang
    nahan TCP MJPEG socket sehingga `recognize_frames` / `qr_frames`
    generator tidak exit, kamera tetap nyala. Endpoint ini memaksa
    cleanup: subscriber count di-reset, worker thread di-stop, kamera
    di-release. Localhost-only.
    """
    _require_localhost(request)
    try:
        recognize_broadcaster.force_stop()
    except Exception:
        app_logger.exception("camera broadcaster release failed")
    try:
        camera_hub.force_stop()
    except Exception:
        app_logger.exception("camera hub release failed")
    return {"ok": True}


@app.post("/api/system/exit-kiosk")
def exit_kiosk(request: Request):
    """
    Kill semua process Chromium yang dijalankan oleh user yang sama
    dengan backend (typically `admin` di Pi). Hanya bisa dipanggil
    dari localhost supaya akses dari laptop/HP lain di jaringan tidak
    bisa iseng matikan tampilan gate.
    """
    _require_localhost(request)

    try:
        # Non-blocking; pkill return cepat walau ada banyak process.
        subprocess.Popen(["pkill", "-f", "chromium"])
        return {"ok": True, "message": "Chromium kiosk dimatikan."}
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="pkill tidak tersedia di sistem.",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/settings")
def get_settings(request: Request):
    require_admin(request)
    return load_settings()


@app.post("/api/settings")
def update_settings(patch: SettingsPatch, request: Request):
    require_admin(request)
    settings = load_settings()
    dump = getattr(patch, "model_dump", patch.dict)
    update = dump(exclude_none=True)
    for key, value in update.items():
        settings[key] = value
    save_settings(settings)
    return settings


@app.post("/api/qr/generate-all")
def generate_all_qr(request: Request):
    require_admin(request)
    manager = QRCodeManager(db_dir=str(FACE_DB_DIR), qr_dir=str(QR_DIR))
    count = manager.generate_qr_for_all()
    return {"ok": True, "generated": count, "qr_count": qr_count()}


@app.post("/api/qr/generate/{nis}")
def generate_qr(nis: str, request: Request):
    require_admin(request)
    result = db_call(lambda db: db.get_parent_by_nis(nis.strip()))
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    if not result["data"]:
        raise HTTPException(status_code=409, detail="QR hanya dibuat untuk siswa yang parent-nya sudah enroll.")

    manager = QRCodeManager(db_dir=str(FACE_DB_DIR), qr_dir=str(QR_DIR))
    ok = manager.generate_qr_code(nis.strip(), silent=True)
    return {"ok": ok, "qr_count": qr_count()}


@app.get("/api/qr/image/{nis}")
def get_qr_image(nis: str, request: Request):
    """Serve QR PNG image untuk siswa. Auto-generate kalau belum ada."""
    require_admin(request)
    nis = nis.strip()
    student_result = db_call(lambda db: db.get_student(nis))
    if not student_result["ok"]:
        raise HTTPException(status_code=503, detail=student_result["error"])
    student = student_result["data"]
    if not student:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan.")

    manager = QRCodeManager(db_dir=str(FACE_DB_DIR), qr_dir=str(QR_DIR))
    # Path file QR. Pakai _qr_filepath agar konsisten dengan logic generate
    qr_path = manager._qr_filepath(nis, student.get("kelas", ""))
    if not os.path.exists(qr_path):
        # Belum ada → generate dulu (butuh parent ter-enroll)
        parent_result = db_call(lambda db: db.get_parent_by_nis(nis))
        if not parent_result["ok"] or not parent_result["data"]:
            raise HTTPException(
                status_code=409,
                detail="QR hanya bisa di-generate untuk siswa yang ortu-nya sudah enroll wajahnya.",
            )
        if not manager.generate_qr_code(nis, silent=True):
            raise HTTPException(status_code=500, detail="Gagal generate QR.")
    return FileResponse(qr_path, media_type="image/png",
                        filename=f"qr_{nis}.png")


@app.get("/api/voice/events")
def voice_events(after: int = 0):
    with runtime.voice_lock:
        events = [event for event in runtime.voice_events if int(event["id"]) > int(after)]
        server_max_id = runtime.voice_event_id
    # `server_max_id` membantu klien mendeteksi server restart: counter
    # voice_event_id reset ke 0 setiap proses uvicorn baru, sedangkan
    # klien menyimpan `lastVoiceEventId` di localStorage. Tanpa info ini
    # klien dengan `after` lebih besar dari counter server tidak akan
    # pernah dapat event lagi sampai counter menyusul.
    return {"events": events[-5:], "server_max_id": server_max_id}


@app.get("/api/voice/audio/{filename}")
def voice_audio(filename: str):
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2})?_[0-9a-f]{24}\.mp3", filename):
        raise HTTPException(status_code=404, detail="Audio tidak ditemukan.")
    path = (TTS_CACHE_DIR / filename).resolve()
    try:
        path.relative_to(TTS_CACHE_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Audio tidak ditemukan.") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio tidak ditemukan.")
    return FileResponse(str(path), media_type="audio/mpeg")


@app.post("/api/voice/test")
def test_voice(payload: VoiceTestIn, request: Request):
    require_admin(request)
    settings = load_settings()
    text = " ".join((payload.text or settings.get("voice_announcement_template") or "Ananda Contoh telah dijemput.").split())
    if "{" in text:
        sample_parent = {
            "nis": "TEST",
            "nama_anak": "Contoh",
            "nama_ortu": "Orang Tua Contoh",
            "kelas": "1A",
        }
        text = voice_text(sample_parent, {**settings, "voice_announcement_template": text})
    audio_url = tts_audio_url(text, settings.get("voice_announcement_language", "id"))
    if not audio_url:
        raise HTTPException(status_code=503, detail="Gagal membuat audio TTS. Pastikan gTTS terinstall dan internet tersedia.")
    with runtime.voice_lock:
        runtime.voice_event_id += 1
        event = {
            "id": runtime.voice_event_id,
            "nis": "TEST",
            "nama_anak": "Contoh",
            "nama_ortu": "Orang Tua Contoh",
            "kelas": "1A",
            "text": text,
            "audio_url": audio_url,
            "created_at": int(time.time()),
        }
        runtime.voice_events.append(event)
    return {"ok": True, "event": event}


@app.post("/api/voice/pregenerate-all")
def pregenerate_all_voice(request: Request):
    """
    Isi cache MP3 untuk semua siswa di kedua kategori. File yang sudah ada
    dilewati, jadi klik ulang hanya membuat suara baru/berubah.
    """
    require_admin(request)
    if not _voice_cache_batch_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Generate voice sedang berjalan. Tunggu sampai selesai.",
        )
    try:
        return _pregenerate_all_voice_locked()
    finally:
        _voice_cache_batch_lock.release()


def _pregenerate_all_voice_locked() -> Dict[str, Any]:
    settings = load_settings()
    if gTTS is None:
        raise HTTPException(
            status_code=503,
            detail="gTTS belum terinstall. Jalankan pip install -r requirements.txt.",
        )

    students_result = db_call(lambda db: db.list_all_students())
    if not students_result["ok"]:
        raise HTTPException(status_code=503, detail=students_result["error"])
    parents_result = db_call(lambda db: db.list_all_parents())
    if not parents_result["ok"]:
        raise HTTPException(status_code=503, detail=parents_result["error"])

    parent_by_nis: Dict[str, Dict[str, Any]] = {}
    for parent in parents_result["data"]:
        parent_by_nis.setdefault(str(parent.get("nis", "")), parent)

    language = settings.get("voice_announcement_language", "id")
    summary: Dict[str, Any] = {
        "students": len(students_result["data"]),
        "targets": 0,
        "generated": 0,
        "skipped": 0,
        "failed": [],
    }
    for student in students_result["data"]:
        nis = str(student.get("nis", ""))
        parent = parent_by_nis.get(nis, {})
        voice_data = {
            "nis": nis,
            "nama_anak": student.get("nama", ""),
            "nama_ortu": parent.get("nama_ortu", ""),
            "kelas": student.get("kelas", ""),
        }
        for category in VOICE_CATEGORY_KEYS:
            summary["targets"] += 1
            text = voice_text(voice_data, settings, category=category)
            cache_path, _ = tts_cache_target(text, language)
            if cache_path.exists():
                summary["skipped"] += 1
                continue
            if tts_audio_url(text, language):
                summary["generated"] += 1
            else:
                summary["failed"].append({"nis": nis, "category": category})

    app_logger.info(
        "voice cache batch complete students=%s targets=%s generated=%s "
        "skipped=%s failed=%s",
        summary["students"],
        summary["targets"],
        summary["generated"],
        summary["skipped"],
        len(summary["failed"]),
    )
    return {"ok": not summary["failed"], **summary}


@app.get("/api/logs")
def attendance_logs(request: Request, limit: int = 100):
    require_admin(request)
    result = db_call(lambda db: db.list_attendance_logs(limit))
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    return serialize_log_rows(result["data"], APP_ROOT)


@app.post("/api/logs/{log_id}/cancel")
def cancel_attendance_log(log_id: int, payload: CancelLogIn, request: Request):
    require_admin(request)
    result = db_call(lambda db: db.cancel_attendance_log(log_id, payload.reason))
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    if not result["data"]:
        raise HTTPException(status_code=404, detail="Log tidak ditemukan atau sudah dibatalkan.")
    return {"ok": True}


@app.get("/api/logs/evidence/{photo_path:path}")
def attendance_log_evidence(photo_path: str, request: Request):
    require_admin(request)
    target = (APP_ROOT / photo_path).resolve()
    try:
        target.relative_to(APP_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Foto tidak ditemukan.") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Foto tidak ditemukan.")
    return FileResponse(str(target))


@app.get("/api/logs/export")
def export_logs(
    request: Request,
    jenis: str,
    excel: bool = True,
    images: bool = False,
    date: str = "",
    student: str = "",
    kelas: str = "",
):
    """Export log per jenis ke .xlsx (opsional + foto bukti → .zip).

    Honor filter aktif (date/student/kelas) sama spt tampilan tabel. Kalau
    `images=true`, tiap foto di-rename `{Nama}_{waktu}.{ext}` dan dibungkus zip
    bareng xlsx. Localhost/admin-only via require_admin.
    """
    require_admin(request)
    import io
    import zipfile
    from datetime import datetime

    allowed = {"KEHADIRAN_QR", "PENJEMPUTAN_QR", "PENJEMPUTAN_FACE", "UNKNOWN_FACE"}
    if jenis not in allowed:
        raise HTTPException(status_code=400, detail="Jenis log tidak valid.")
    if not excel and not images:
        raise HTTPException(status_code=400, detail="Pilih minimal satu: Excel atau Gambar.")

    result = db_call(lambda db: db.list_attendance_logs(500))
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])

    student_q = student.strip().lower()
    kelas_q = kelas.strip().lower()
    rows = []
    for r in result["data"]:
        if r.get("jenis_absen") != jenis:
            continue
        waktu = r.get("waktu_absen")
        waktu_str = (
            waktu.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(waktu, datetime) else str(waktu or "")
        )
        if date and waktu_str[:10] != date:
            continue
        if student_q and student_q not in str(r.get("nama_siswa") or "").lower():
            continue
        if kelas_q and kelas_q not in str(r.get("kelas") or "").lower():
            continue
        rows.append((r, waktu, waktu_str))

    if not rows:
        raise HTTPException(status_code=404, detail="Tidak ada data log untuk diexport (cek filter).")

    # Rencana nama file foto unik + resolve path disk (aman di dalam APP_ROOT).
    app_root_resolved = APP_ROOT.resolve()
    photo_plan = []  # sejajar rows: (arcname|None, disk_path|None)
    used_names: Dict[str, int] = {}
    for (r, waktu, waktu_str) in rows:
        arc = None
        disk = None
        raw = r.get("bukti_foto")
        if raw:
            p = Path(raw)
            if not p.is_absolute():
                p = APP_ROOT / p
            try:
                p = p.resolve()
                p.relative_to(app_root_resolved)
            except (ValueError, OSError):
                p = None
            if p and p.exists() and p.is_file():
                disk = p
                nama = r.get("nama_siswa") or r.get("nis") or "UNKNOWN"
                ts = waktu.strftime("%Y%m%d_%H%M%S") if isinstance(waktu, datetime) else "0"
                stem = safe_filename_part(f"{nama}_{ts}")
                ext = p.suffix.lower() or ".jpg"
                name = f"{stem}{ext}"
                seen = used_names.get(name, 0)
                used_names[name] = seen + 1
                if seen:
                    name = f"{stem}_{seen + 1}{ext}"
                arc = name
        photo_plan.append((arc, disk))

    base = f"log_{jenis.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Build xlsx kalau diminta.
    xlsx_bytes = None
    if excel:
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail="openpyxl belum terinstall di server — export Excel tidak tersedia.",
            ) from exc
        wb = Workbook()
        ws = wb.active
        ws.title = "Log"
        ws.append([
            "No", "NIS", "Nama Siswa", "Kelas", "Jenis", "Waktu Absen",
            "Status", "Alasan Batal", "Waktu Batal", "Nama File Foto",
        ])
        for i, ((r, waktu, waktu_str), (arc, _disk)) in enumerate(zip(rows, photo_plan), start=1):
            cancelled = r.get("cancelled_at")
            cancelled_str = (
                cancelled.strftime("%Y-%m-%d %H:%M:%S")
                if isinstance(cancelled, datetime) else (cancelled or "")
            )
            ws.append([
                i, r.get("nis"), r.get("nama_siswa") or "", r.get("kelas") or "",
                r.get("jenis_absen"), waktu_str,
                "TIDAK VALID" if r.get("status") == "DIBATALKAN" else "VALID",
                r.get("cancel_reason") or "", cancelled_str, arc or "",
            ])
        for idx, width in enumerate([5, 14, 24, 10, 16, 20, 12, 24, 20, 28], start=1):
            ws.column_dimensions[chr(64 + idx)].width = width
        buf = io.BytesIO()
        wb.save(buf)
        xlsx_bytes = buf.getvalue()

    # Excel saja → kirim .xlsx.
    if not images:
        return Response(
            content=xlsx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{base}.xlsx"'},
        )

    # Ada gambar → bungkus zip (xlsx opsional + folder foto/).
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        if xlsx_bytes is not None:
            zf.writestr(f"{base}.xlsx", xlsx_bytes)
        for (arc, disk) in photo_plan:
            if arc and disk:
                zf.write(str(disk), f"foto/{arc}")
    return Response(
        content=zbuf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{base}.zip"'},
    )


@app.post("/api/enroll/start")
def enroll_start(payload: EnrollStart, request: Request):
    require_admin(request)
    settings = load_settings()

    result = db_call(lambda db: db.get_student(payload.nis.strip()))
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    student = result["data"]
    if not student:
        raise HTTPException(status_code=404, detail="NIS tidak ditemukan di database.")

    session_id = str(uuid.uuid4())
    runtime.enroll_sessions[session_id] = {
        "nis": payload.nis.strip(),
        "parent_name": payload.parent_name.strip(),
        "student": student,
        "target": payload.samples or int(settings["samples"]),
        "embeddings": [],
        "snapshots": [],
        "created_at": time.time(),
    }

    return {
        "session_id": session_id,
        "target": runtime.enroll_sessions[session_id]["target"],
        "captured": 0,
        "student": student,
    }


@app.post("/api/enroll/capture")
def enroll_capture(payload: FrameCapture, request: Request):
    require_admin(request)
    session = runtime.enroll_sessions.get(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesi enroll tidak ditemukan.")

    settings = load_settings()
    image_text = payload.image_data
    if "," in image_text:
        image_text = image_text.split(",", 1)[1]

    try:
        image_bytes = base64.b64decode(image_text)
        image_arr = np.frombuffer(image_bytes, dtype=np.uint8)
        frame = cv2.imdecode(image_arr, cv2.IMREAD_COLOR)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Frame tidak valid: {exc}") from exc

    if frame is None:
        raise HTTPException(status_code=400, detail="Frame tidak valid.")

    face_app = runtime.get_face_app(settings)
    faces = face_app.get(frame)
    face = pick_largest_face(faces)
    min_score = float(settings["min_det_score"])
    if face is None or float(face.det_score) < min_score:
        raise HTTPException(status_code=422, detail="Wajah belum terdeteksi jelas.")

    session["embeddings"].append(face.normed_embedding.astype(np.float32))
    session["snapshots"].append(frame)
    captured = len(session["embeddings"])
    target = int(session["target"])

    if captured < target:
        return {"complete": False, "captured": captured, "target": target}

    avg_emb = np.mean(np.stack(session["embeddings"], axis=0), axis=0)
    avg_emb = l2_normalize(avg_emb)

    label = f"{session['parent_name']}_{session['student']['nama']}_{session['student']['kelas']}"
    db = FaceDB(str(FACE_DB_DIR))
    embedding_index = db.add(avg_emb)

    result = db_call(lambda student_db: student_db.add_parent(
        session["nis"],
        session["parent_name"],
        embedding_index,
    ))
    if not result["ok"]:
        # Rollback: hapus embedding yang sudah tersimpan
        db.remove(embedding_index)
        raise HTTPException(
            status_code=503,
            detail=f"MySQL gagal, embedding di-rollback: {result['error']}",
        )

    snap_dir = FACE_DB_DIR / "snapshots" / label.replace(" ", "_")
    snap_dir.mkdir(parents=True, exist_ok=True)
    for index, snapshot in enumerate(session["snapshots"], 1):
        cv2.imwrite(str(snap_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{index}.jpg"), snapshot)

    QRCodeManager(db_dir=str(FACE_DB_DIR), qr_dir=str(QR_DIR)).generate_qr_code(session["nis"], silent=True)
    runtime.enroll_sessions.pop(payload.session_id, None)

    # Pre-generate TTS audio untuk siswa ini di kedua kategori (kehadiran &
    # penjemputan) sehingga saat offline nanti tidak perlu memanggil gTTS
    # lagi. Internet biasanya masih tersedia di komputer admin saat
    # enroll, jadi cache dipopulate di sini lebih reliable daripada di
    # gate saat kondisi internet sekolah nggak menentu.
    try:
        pregenerate_voice_cache_for_student(
            {
                "nis": session["nis"],
                "nama": session["student"]["nama"],
                "nama_ortu": session["parent_name"],
                "kelas": session["student"]["kelas"],
            }
        )
    except Exception:
        app_logger.exception("voice cache generation after enrollment failed")

    return {
        "complete": True,
        "captured": captured,
        "target": target,
        "embedding_index": embedding_index,
    }


def preview_frames():
    settings = load_settings()
    camera_hub.acquire(settings)
    get_led().off()  # preview = no scan, semua LED mati
    try:
        while True:
            frame = camera_hub.get_frame(mirror=bool(settings.get("mirror_camera")))
            yield mjpeg_part(frame)
            time.sleep(0.04)
    finally:
        camera_hub.release()


def recognize_frames():
    """Subscriber tipis ke `recognize_broadcaster`.

    Inference + drawing dijalankan oleh 1 worker thread di broadcaster
    (lihat class RecognizeBroadcaster). Banyak client (Pi LCD + laptop
    admin) bisa connect barengan, masing-masing dapat frame yang sama
    tanpa overload CPU karena inference cuma 1×.
    """
    settings = load_settings()
    recognize_broadcaster.subscribe(settings)
    try:
        last_seq = 0
        while True:
            # Kalau broadcaster di-force-stop dari endpoint /api/camera/release,
            # _running=False — break biar generator exit clean (jangan infinite
            # loop kalau wait_frame selalu None).
            if not recognize_broadcaster._running:
                break
            result = recognize_broadcaster.wait_frame(last_seq, timeout=3.0)
            if result is None:
                continue
            frame_bytes, last_seq = result
            yield frame_bytes
    finally:
        recognize_broadcaster.unsubscribe()


def qr_frames(mode: str):
    settings = load_settings()
    manager = QRCodeManager(db_dir=str(FACE_DB_DIR), qr_dir=str(QR_DIR))
    stats = StreamStats()
    frame_count = 0
    last_result = None
    db_error = None
    mode = "attendance" if mode == "attendance" else "pickup"
    jenis_absen = "KEHADIRAN_QR" if mode == "attendance" else "PENJEMPUTAN_QR"
    mode_label = "QR Kehadiran Siswa" if mode == "attendance" else "QR Penjemputan Non-orang Tua"
    face_app = None

    try:
        student_db = StudentDatabase()
    except Exception as exc:
        student_db = None
        db_error = str(exc)

    try:
        face_app = runtime.get_face_app(settings)
    except Exception as exc:
        db_error = f"Face detection error: {exc}"

    camera_hub.acquire(settings)
    led = get_led()
    led.set_idle()
    try:
        while True:
            # Force-stop dari /api/camera/release (user balik ke home):
            # camera_hub.force_stop() set _running=False. Tanpa cek ini loop
            # tetap jalan pakai frame basi (_latest_frame tak di-clear) → finally
            # led.off() tak kepanggil → LED orange nyala terus walau kamera mati.
            if not camera_hub._running:
                break
            frame_start = time.perf_counter()
            frame = camera_hub.get_frame(mirror=bool(settings.get("mirror_camera")))
            frame_count += 1
            disp = frame.copy()

            if db_error:
                draw_status_text(disp, [f"MySQL error: {db_error[:55]}"], ok=False)
            elif frame_count % 3 == 0:
                last_result = decode_qr_frame(frame, manager)

                if last_result and student_db and face_app:
                    nis = last_result.get("nis") or "-"
                    valid = bool(last_result.get("verified"))
                    student = student_db.get_student(nis) if valid else None
                    parent = student_db.get_parent_by_nis(nis) if valid and mode == "pickup" else None
                    face = None
                    face_detected = False
                    if valid:
                        infer_frame, infer_scale = _prepare_inference_frame(frame)
                        faces = face_app.get(infer_frame)
                        face = pick_largest_face(faces)
                        if face is not None and infer_scale != 1.0:
                            face.bbox = face.bbox.astype(np.float32) * infer_scale
                        face_detected = face is not None and float(face.det_score) >= float(settings["min_det_score"])
                        if not face_detected:
                            last_result["status_lines"] = [
                                "QR valid, wajah belum terdeteksi",
                                f"NIS: {nis}",
                            ]
                            last_result["ok"] = False

                    if mode == "attendance" and student and face_detected:
                        key = f"{jenis_absen}:{nis}"
                        text = f"Siswa: {student['nama']} ({student['kelas']})\nNIS: {nis}"
                        if enqueue_recognition_side_effects(
                            student_db,
                            {
                                "nis": nis,
                                "nama_anak": student["nama"],
                                "kelas": student["kelas"],
                            },
                            settings,
                            key,
                            frame,
                            face,
                            text,
                            jenis_absen,
                            nis,
                            voice_category="attendance",
                        ):
                            pass  # side-effects enqueued (attendance evidence + voice)
                        last_result["status_lines"] = [
                            f"Siswa: {student['nama']} ({student['kelas']})",
                            f"NIS: {nis}",
                        ]
                        last_result["ok"] = True
                    elif mode == "pickup" and parent and face_detected:
                        key = f"{jenis_absen}:{nis}"
                        text = f"Anak: {parent['nama_anak']} ({parent['kelas']})\nNIS: {nis}"
                        if enqueue_recognition_side_effects(
                            student_db,
                            parent,
                            settings,
                            key,
                            frame,
                            face,
                            text,
                            jenis_absen,
                            nis,
                            voice_category="pickup",
                        ):
                            pass  # side-effects enqueued (pickup evidence + voice)
                        last_result["status_lines"] = [
                            f"Siswa: {parent['nama_anak']} ({parent['kelas']})",
                            f"NIS: {nis}",
                        ]
                        last_result["ok"] = True
                    elif not valid or (mode == "attendance" and not student) or (mode == "pickup" and not parent):
                        last_result["status_lines"] = [
                            "QR tidak valid untuk mode ini",
                            f"NIS: {nis}",
                        ]
                        last_result["ok"] = False

            if last_result:
                draw_status_text(
                    disp,
                    last_result.get("status_lines", ["QR terdeteksi"]),
                    ok=bool(last_result.get("ok")),
                )
                if last_result.get("ok"):
                    draw_success_badge(disp)
                    led.set_recognized()
                elif last_result.get("ok") is False:
                    led.set_unknown()
            else:
                draw_status_text(disp, [mode_label, "Arahkan QR ke kamera"])

            stats.add_frame(time.perf_counter() - frame_start)
            if settings.get("show_performance", True):
                draw_performance_overlay(disp, stats.overlay_text())

            yield mjpeg_part(disp)
            time.sleep(0.04)
    finally:
        led.off()
        camera_hub.release()


@app.get("/video/preview")
def video_preview():
    return StreamingResponse(preview_frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/video/recognize")
def video_recognize():
    return StreamingResponse(recognize_frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/video/qr")
def video_qr(mode: str = "attendance"):
    return StreamingResponse(qr_frames(mode), media_type="multipart/x-mixed-replace; boundary=frame")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="127.0.0.1", port=8000, reload=True)
