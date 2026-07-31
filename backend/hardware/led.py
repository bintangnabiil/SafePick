"""LED indicator 3-warna (hijau/kuning/merah) untuk SafePick.

Wiring di Raspberry Pi 4 (sudah diverifikasi):
    Modul wire     Pi pin (header)   GPIO (BCM)
    -------------  ----------------  ----------
    G (hijau)      32                GPIO 12
    Y (kuning)     36                GPIO 16
    R (merah)      38                GPIO 20
    GND            34                Ground

State machine:
    - off         : semua LED mati (preview / app stop)
    - idle        : KUNING ON     (stream recognize/QR aktif, idle scan)
    - recognized  : HIJAU ON      (auto-revert ke idle setelah HOLD_SECONDS)
    - unknown     : MERAH ON      (auto-revert ke idle setelah HOLD_SECONDS)

Thread-safe. Auto no-op kalau:
    - gpiozero tidak ter-install (mis. running di laptop Windows)
    - GPIO init gagal (mis. permission denied)
    - Environment variable SAFEPICK_LED_ENABLED=false
"""

from __future__ import annotations

import os
import threading
from typing import Optional

try:
    from gpiozero import LED  # type: ignore
    _GPIOZERO_AVAILABLE = True
except Exception:
    _GPIOZERO_AVAILABLE = False
    LED = None  # type: ignore


# Pin mapping (BCM numbering, sesuai wiring fisik yang sudah diverifikasi)
PIN_GREEN = 12   # Pi pin 32 - wire G modul
PIN_YELLOW = 16  # Pi pin 36 - wire Y modul
PIN_RED = 20     # Pi pin 38 - wire R modul

# Lama LED hijau/merah ditahan sebelum auto-revert ke kuning (idle)
DEFAULT_HOLD_SECONDS = 2.5


class LedIndicator:
    """3-LED traffic light indicator. Thread-safe singleton-friendly."""

    def __init__(
        self,
        pin_green: int = PIN_GREEN,
        pin_yellow: int = PIN_YELLOW,
        pin_red: int = PIN_RED,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        enabled: bool = True,
    ) -> None:
        self._hold = float(hold_seconds)
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._state: str = "off"
        self._enabled = bool(enabled) and _GPIOZERO_AVAILABLE

        self._green = None
        self._yellow = None
        self._red = None

        if self._enabled:
            try:
                self._green = LED(pin_green)
                self._yellow = LED(pin_yellow)
                self._red = LED(pin_red)
            except Exception as exc:
                print(f"[led] init failed: {exc} - LED dinonaktifkan")
                self._enabled = False
                self._green = self._yellow = self._red = None

    # ---- public API --------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def enabled(self) -> bool:
        return self._enabled

    def off(self) -> None:
        """Matikan semua LED, batalkan auto-revert."""
        with self._lock:
            self._cancel_timer_locked()
            self._apply("off")

    def set_idle(self) -> None:
        """Kuning ON (stream aktif, sedang scan)."""
        with self._lock:
            self._cancel_timer_locked()
            self._apply("idle")

    def set_recognized(self) -> None:
        """Hijau ON sebentar, lalu kembali ke idle (kuning)."""
        with self._lock:
            self._cancel_timer_locked()
            self._apply("recognized")
            self._schedule_revert_locked()

    def set_unknown(self) -> None:
        """Merah ON sebentar, lalu kembali ke idle (kuning)."""
        with self._lock:
            self._cancel_timer_locked()
            self._apply("unknown")
            self._schedule_revert_locked()

    def close(self) -> None:
        """Cleanup GPIO. Dipanggil saat aplikasi shutdown."""
        with self._lock:
            self._cancel_timer_locked()
        if not self._enabled:
            return
        try:
            self._apply("off")
        except Exception:
            pass
        for led in (self._green, self._yellow, self._red):
            try:
                if led is not None:
                    led.close()
            except Exception:
                pass

    # ---- internals ---------------------------------------------------------

    def _apply(self, state: str) -> None:
        self._state = state
        if not self._enabled:
            return
        # (green, yellow, red)
        mapping = {
            "off":        (False, False, False),
            "idle":       (False, True,  False),
            "recognized": (True,  False, False),
            "unknown":    (False, False, True),
        }
        g, y, r = mapping.get(state, (False, False, False))
        try:
            (self._green.on if g else self._green.off)()
            (self._yellow.on if y else self._yellow.off)()
            (self._red.on if r else self._red.off)()
        except Exception as exc:
            print(f"[led] apply state '{state}' failed: {exc}")

    def _schedule_revert_locked(self) -> None:
        t = threading.Timer(self._hold, self._revert_to_idle)
        t.daemon = True
        self._timer = t
        t.start()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:
                pass
            self._timer = None

    def _revert_to_idle(self) -> None:
        with self._lock:
            # Hanya revert kalau state masih sukses/denied (bukan off/idle)
            if self._state in ("recognized", "unknown"):
                self._apply("idle")


# ---- module-level singleton ------------------------------------------------

_singleton: Optional[LedIndicator] = None
_singleton_lock = threading.Lock()


def get_led(enabled: Optional[bool] = None) -> LedIndicator:
    """Return global LedIndicator instance (singleton).

    Disabled by env: SAFEPICK_LED_ENABLED=false
    """
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            if enabled is None:
                env = os.environ.get("SAFEPICK_LED_ENABLED", os.environ.get("FACEGATE_LED_ENABLED", "true")).lower()
                enabled = env in ("1", "true", "yes", "on")
            _singleton = LedIndicator(enabled=enabled)
        return _singleton


def shutdown_led() -> None:
    """Tutup singleton (dipanggil di lifespan shutdown)."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            try:
                _singleton.close()
            finally:
                _singleton = None
