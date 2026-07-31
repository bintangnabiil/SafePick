"""
Volume sistem via PipeWire/PulseAudio (pactl).

Pi pakai PipeWire (PulseAudio shim). Audio asli — announcement TTS yang
diputar Chromium, speaker bluetooth A2DP — keluar lewat *default sink*
PipeWire, BUKAN ALSA "Master" (amixer cuma atur card0 headphone yang
sering tak terpakai). Karena itu kontrol volume harus lewat pactl ke
`@DEFAULT_SINK@` supaya benar-benar mengubah output yang didengar.

pactl butuh akses ke session bus PipeWire user (uid 1000). Service
safepick-web jalan sebagai User=admin tapi tanpa XDG_RUNTIME_DIR, jadi
kita inject `XDG_RUNTIME_DIR=/run/user/<uid>` di tiap pemanggilan.

Semua endpoint volume di backend/app.py di-restrict ke localhost (127.0.0.1)
seperti wifi/bluetooth — yang fisik pegang LCD touchscreen yang berhak atur.
"""

import os
import re
import subprocess
import time
from typing import Dict, Optional

from backend.utils.logger import get_app_logger


logger = get_app_logger()

_SINK = "@DEFAULT_SINK@"

_PCT_RE = re.compile(r"(\d+)%")
_MUTE_RE = re.compile(r"Mute:\s*(yes|no)", re.IGNORECASE)

_sink_name_cache: Optional[str] = None


class AmixerNotAvailable(RuntimeError):
    """Raised saat pactl tidak tersedia (mis. dev di Windows) / tak bisa connect."""


# Alias nama yang lebih tepat; AmixerNotAvailable dipertahankan untuk app.py.
VolumeUnavailable = AmixerNotAvailable


def _runtime_env() -> Dict[str, str]:
    """Env untuk pactl: warisi env sekarang + paksa XDG_RUNTIME_DIR user."""
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR"):
        try:
            uid = os.getuid()  # type: ignore[attr-defined]
        except AttributeError:
            uid = 1000  # Windows dev — pactl absen, error ditangani caller.
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    return env


def _pactl(*args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            ["pactl"] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_runtime_env(),
        )
    except subprocess.TimeoutExpired:
        logger.exception("pactl timeout args=%s timeout=%s", list(args), timeout)
        raise
    except FileNotFoundError as exc:
        logger.exception("pactl executable not found")
        raise AmixerNotAvailable(
            "pactl tidak terinstall — fitur volume hanya tersedia di Linux dengan PipeWire/PulseAudio."
        ) from exc
    duration_ms = (time.perf_counter() - started) * 1000
    if result.returncode != 0:
        logger.warning(
            "pactl failed args=%s code=%s duration_ms=%.1f stderr=%s",
            list(args),
            result.returncode,
            duration_ms,
            (result.stderr or "").strip()[:1000],
        )
    else:
        logger.debug("pactl ok args=%s duration_ms=%.1f", list(args), duration_ms)
    if result.returncode != 0 and "Connection refused" in (result.stderr or ""):
        raise AmixerNotAvailable(
            "Tidak bisa connect ke PipeWire/PulseAudio (XDG_RUNTIME_DIR salah?)."
        )
    return result


def _default_sink_name() -> str:
    """Nama default sink (buat label UI), cache hasilnya."""
    global _sink_name_cache
    if _sink_name_cache:
        return _sink_name_cache
    name = _pactl("get-default-sink").stdout.strip()
    _sink_name_cache = name or "default"
    return _sink_name_cache


def status() -> Dict:
    """Volume saat ini: {control, percent (0-100), muted}."""
    vol_out = _pactl("get-sink-volume", _SINK).stdout
    mute_out = _pactl("get-sink-mute", _SINK).stdout
    pct_match = _PCT_RE.search(vol_out)
    mute_match = _MUTE_RE.search(mute_out)
    percent = int(pct_match.group(1)) if pct_match else 0
    muted = bool(mute_match) and mute_match.group(1).lower() == "yes"
    return {"control": _default_sink_name(), "percent": percent, "muted": muted}


def set_volume(percent: int) -> Dict:
    """Set default sink ke persen (0-100). Unmute otomatis kalau > 0."""
    percent = max(0, min(100, int(percent)))
    _pactl("set-sink-volume", _SINK, f"{percent}%")
    _pactl("set-sink-mute", _SINK, "0" if percent > 0 else "1")
    return status()


def set_muted(muted: bool) -> Dict:
    """Mute / unmute default sink."""
    _pactl("set-sink-mute", _SINK, "1" if muted else "0")
    return status()
