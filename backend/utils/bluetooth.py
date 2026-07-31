"""
Bluetooth management via bluetoothctl (BlueZ CLI).

Pi OS Trixie sudah include BlueZ stack. User `admin` di group `bluetooth`
bisa drive bluetoothctl tanpa sudo.

Use case utama untuk SafePick:
- Connect Bluetooth speaker untuk TTS announcement yang lebih kencang.
- Pairing keyboard/mouse (jarang, virtual keyboard sudah ada).

Semua endpoint bluetooth di backend/app.py di-restrict ke localhost
(127.0.0.1) supaya orang dari LAN tidak bisa iseng pair speaker — yang
fisik akses ke Pi (LCD touchscreen) lah yang berhak.

Implementasi pakai `bluetoothctl --timeout N command` (one-shot, non-interactive)
dan parsing output baris demi baris. Tidak pakai dbus library supaya tidak
nambah dependency.
"""

import re
import subprocess
import time
from typing import Dict, List, Optional

from backend.utils.logger import get_app_logger


logger = get_app_logger()

# Pola MAC + nama device di output `devices`:
#   "Device AA:BB:CC:DD:EE:FF Speaker Name"
_DEVICE_LINE = re.compile(
    r"^Device\s+([0-9A-Fa-f:]{17})\s+(.*)$", re.MULTILINE
)


class BluetoothctlNotAvailable(RuntimeError):
    """Raised when bluetoothctl binary tidak terinstall (mis. dev di Windows)."""


def _bt(*args: str, timeout: int = 15, stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run `bluetoothctl <args>` (one-shot, non-interactive)."""
    started = time.perf_counter()
    try:
        result = subprocess.run(
            ["bluetoothctl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
        )
        duration_ms = (time.perf_counter() - started) * 1000
        if result.returncode != 0:
            logger.warning(
                "bluetoothctl failed args=%s code=%s duration_ms=%.1f stderr=%s",
                list(args),
                result.returncode,
                duration_ms,
                (result.stderr or "").strip()[:1000],
            )
        else:
            logger.debug(
                "bluetoothctl ok args=%s duration_ms=%.1f",
                list(args),
                duration_ms,
            )
        return result
    except subprocess.TimeoutExpired:
        logger.exception("bluetoothctl timeout args=%s timeout=%s", list(args), timeout)
        raise
    except FileNotFoundError as exc:
        logger.exception("bluetoothctl executable not found")
        raise BluetoothctlNotAvailable(
            "bluetoothctl tidak terinstall — fitur Bluetooth hanya tersedia di Linux dengan BlueZ."
        ) from exc


def _parse_devices(output: str) -> List[Dict]:
    """Parse output `devices` atau `devices Paired/Connected/...`."""
    devices: List[Dict] = []
    for mac, name in _DEVICE_LINE.findall(output):
        devices.append({"mac": mac.upper(), "name": name.strip()})
    return devices


def _ensure_powered() -> None:
    """Pastikan adapter Bluetooth nyala."""
    _bt("power", "on", timeout=5)


def status() -> Dict:
    """
    Status adapter + list device yang sedang ter-connect.
    Return: {
        "powered": bool,
        "discoverable": bool,
        "connected": [{"mac": "...", "name": "..."}]
    }
    """
    powered = False
    discoverable = False
    show = _bt("show", timeout=5)
    if show.returncode == 0:
        for line in show.stdout.splitlines():
            if "Powered:" in line:
                powered = "yes" in line.lower()
            elif "Discoverable:" in line:
                discoverable = "yes" in line.lower()

    # bluetoothctl 5.66+ punya `devices Connected`. Kalau gagal (versi lama),
    # fallback ke parse semua + cek individual.
    connected: List[Dict] = []
    res = _bt("devices", "Connected", timeout=5)
    if res.returncode == 0 and res.stdout.strip():
        connected = _parse_devices(res.stdout)
    else:
        # Fallback: list all devices, cek "Connected: yes" per device
        all_devs = _bt("devices", timeout=5)
        for d in _parse_devices(all_devs.stdout):
            info = _bt("info", d["mac"], timeout=5)
            if info.returncode == 0 and re.search(
                r"Connected:\s*yes", info.stdout, re.IGNORECASE
            ):
                connected.append(d)

    return {
        "powered": powered,
        "discoverable": discoverable,
        "connected": connected,
    }


def scan(duration: int = 8) -> List[Dict]:
    """
    Scan device Bluetooth sekitar selama `duration` detik.
    Sebelum scan, adapter di-power on + agent default di-set supaya
    "Just Works" pairing tidak crash di prompt PIN.

    Return: list device dengan field:
        mac (str), name (str), paired (bool), connected (bool),
        trusted (bool), icon (str, mis. "audio-card")
    """
    _ensure_powered()
    # Set agent NoInputNoOutput supaya pairing speaker (no PIN) jalan otomatis
    _bt("agent", "NoInputNoOutput", timeout=3)
    _bt("default-agent", timeout=3)

    # Trigger scan dengan timeout — newer bluetoothctl support `--timeout`.
    # Untuk versi lama yang belum support, fallback ke wrapper `timeout`.
    scan_proc = _bt("--timeout", str(duration), "scan", "on", timeout=duration + 5)
    # Jika --timeout tidak dikenali (output: "Invalid command"), fallback:
    if "Invalid command" in scan_proc.stderr or "unknown option" in scan_proc.stderr.lower():
        try:
            subprocess.run(
                ["timeout", str(duration), "bluetoothctl", "scan", "on"],
                capture_output=True, text=True, timeout=duration + 5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Ambil hasil scan
    all_res = _bt("devices", timeout=5)
    paired_res = _bt("devices", "Paired", timeout=5)
    connected_res = _bt("devices", "Connected", timeout=5)

    all_devs = _parse_devices(all_res.stdout)
    paired_macs = {d["mac"] for d in _parse_devices(paired_res.stdout)}
    connected_macs = {d["mac"] for d in _parse_devices(connected_res.stdout)}

    # Enrich masing-masing device dengan info detail
    results: List[Dict] = []
    seen = set()
    for d in all_devs:
        if d["mac"] in seen:
            continue
        seen.add(d["mac"])
        info_text = _bt("info", d["mac"], timeout=4).stdout
        trusted = bool(re.search(r"Trusted:\s*yes", info_text, re.IGNORECASE))
        # Icon dipakai untuk pilih ikon UI: audio-card, input-keyboard, phone, dll
        icon_match = re.search(r"Icon:\s*(\S+)", info_text)
        icon = icon_match.group(1) if icon_match else ""
        results.append({
            "mac": d["mac"],
            "name": d["name"] or d["mac"],
            "paired": d["mac"] in paired_macs,
            "connected": d["mac"] in connected_macs,
            "trusted": trusted,
            "icon": icon,
        })

    # Sort: connected first, then paired, then by name
    results.sort(key=lambda r: (
        0 if r["connected"] else 1,
        0 if r["paired"] else 1,
        r["name"].lower(),
    ))
    return results


def pair_and_connect(mac: str) -> Dict:
    """
    Pair (kalau belum), trust, dan connect ke device. Cocok untuk speaker
    BT yang "Just Works" pairing tanpa PIN.

    Return: {"ok": bool, "message": str}
    """
    mac = (mac or "").strip().upper()
    if not re.match(r"^[0-9A-F:]{17}$", mac):
        return {"ok": False, "message": f"MAC tidak valid: {mac}"}

    _ensure_powered()
    _bt("agent", "NoInputNoOutput", timeout=3)
    _bt("default-agent", timeout=3)

    # Cek apakah sudah paired
    info = _bt("info", mac, timeout=5).stdout
    is_paired = bool(re.search(r"Paired:\s*yes", info, re.IGNORECASE))

    if not is_paired:
        pair = _bt("pair", mac, timeout=30)
        out = (pair.stdout + pair.stderr).lower()
        if "successful" not in out and "already paired" not in out:
            return {
                "ok": False,
                "message": (pair.stdout + pair.stderr).strip()
                or "Gagal pair (mungkin device meminta PIN atau tidak responsif).",
            }

    # Trust supaya auto-connect saat reboot
    _bt("trust", mac, timeout=5)

    # Connect
    conn = _bt("connect", mac, timeout=20)
    out = (conn.stdout + conn.stderr).lower()
    if "connection successful" in out or "successful" in out:
        return {"ok": True, "message": (conn.stdout + conn.stderr).strip()
                or f"Tersambung ke {mac}."}
    return {
        "ok": False,
        "message": (conn.stdout + conn.stderr).strip() or "Gagal connect.",
    }


def disconnect(mac: str) -> Dict:
    """Disconnect device tapi keep paired (bisa connect lagi nanti)."""
    mac = (mac or "").strip().upper()
    if not re.match(r"^[0-9A-F:]{17}$", mac):
        return {"ok": False, "message": f"MAC tidak valid: {mac}"}

    res = _bt("disconnect", mac, timeout=10)
    out = (res.stdout + res.stderr).strip()
    if res.returncode == 0:
        return {"ok": True, "message": out or "Disconnected."}
    return {"ok": False, "message": out or "Gagal disconnect."}


def unpair(mac: str) -> Dict:
    """Remove pairing (lupakan device sepenuhnya)."""
    mac = (mac or "").strip().upper()
    if not re.match(r"^[0-9A-F:]{17}$", mac):
        return {"ok": False, "message": f"MAC tidak valid: {mac}"}

    res = _bt("remove", mac, timeout=10)
    out = (res.stdout + res.stderr).strip()
    if res.returncode == 0:
        return {"ok": True, "message": out or "Unpaired."}
    return {"ok": False, "message": out or "Gagal unpair."}
