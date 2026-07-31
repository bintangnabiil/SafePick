"""
WiFi management via nmcli (NetworkManager CLI).

Pi OS Trixie pakai NetworkManager untuk WiFi. User `admin` di group
`netdev` bisa scan dan connect tanpa sudo, jadi semua call ke nmcli
di sini berjalan sebagai user backend (tidak ada subprocess sudo).

Semua endpoint wifi di backend/app.py di-restrict ke localhost
(127.0.0.1) supaya orang dari LAN tidak bisa iseng setup WiFi —
yang fisik akses ke Pi (LCD touchscreen) lah yang berhak.
"""

import subprocess
import time
from typing import Dict, List, Optional

from backend.utils.logger import get_app_logger


logger = get_app_logger()


class NmcliNotAvailable(RuntimeError):
    """Raised when nmcli binary is not installed (e.g. dev on Windows)."""


def _nmcli(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Jalankan nmcli dengan output terminator ':' (-t)."""
    started = time.perf_counter()
    try:
        result = subprocess.run(
            ["nmcli", "-t"] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration_ms = (time.perf_counter() - started) * 1000
        if result.returncode != 0:
            logger.warning(
                "nmcli failed args=%s code=%s duration_ms=%.1f stderr=%s",
                list(args),
                result.returncode,
                duration_ms,
                (result.stderr or "").strip()[:1000],
            )
        else:
            logger.debug("nmcli ok args=%s duration_ms=%.1f", list(args), duration_ms)
        return result
    except subprocess.TimeoutExpired:
        logger.exception("nmcli timeout args=%s timeout=%s", list(args), timeout)
        raise
    except FileNotFoundError as exc:
        logger.exception("nmcli executable not found")
        raise NmcliNotAvailable(
            "nmcli tidak terinstall — fitur WiFi hanya tersedia di Linux dengan NetworkManager."
        ) from exc


def scan(rescan: bool = True) -> List[Dict]:
    """
    Scan WiFi networks di sekitar Pi dan kembalikan list yang sudah
    di-dedup + di-sort berdasarkan signal strength.

    Field per network: ssid, signal (0-100), secured (bool),
    connected (bool, True kalau interface sedang nyambung ke ssid ini).
    """
    # `--rescan yes` pada `wifi list` memicu scan baru DAN blok sampai scan
    # selesai, jadi hasilnya fresh. (Sebelumnya `device wifi rescan` dipanggil
    # terpisah lalu langsung `list` → list baca cache lama karena rescan async
    # → hotspot HP/laptop yg baru dinyalakan tak muncul.) Saat wlan0 associated
    # ke 5GHz, scan full butuh waktu lebih (sapu semua channel termasuk 2.4),
    # jadi timeout dilonggarkan.
    list_args = ["-f", "SSID,SIGNAL,SECURITY,IN-USE", "device", "wifi", "list"]
    if rescan:
        list_args += ["--rescan", "yes"]

    result = _nmcli(*list_args, timeout=25)
    if result.returncode != 0:
        return []

    networks: List[Dict] = []
    seen_ssids: set = set()
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 4:
            continue
        ssid, signal, security, in_use = parts[0], parts[1], parts[2], parts[3]
        if not ssid or ssid == "--" or ssid in seen_ssids:
            continue
        seen_ssids.add(ssid)
        networks.append({
            "ssid": ssid,
            "signal": int(signal) if signal.isdigit() else 0,
            "secured": security not in ("", "--"),
            "connected": in_use == "*",
        })
    networks.sort(key=lambda n: n["signal"], reverse=True)
    return networks


def status() -> Dict:
    """
    Kondisi koneksi WiFi saat ini.
    Return: {"connected": bool, "ssid": Optional[str], "device": Optional[str]}
    """
    result = _nmcli(
        "-f", "NAME,DEVICE,STATE,TYPE",
        "connection", "show", "--active",
        timeout=5,
    )
    if result.returncode != 0:
        return {"connected": False, "ssid": None, "device": None}

    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 4:
            continue
        name, device, state, conn_type = parts[0], parts[1], parts[2], parts[3]
        if conn_type == "802-11-wireless" and state == "activated":
            # Ambil SSID asli dari connection profile, bukan nama profile
            ssid = name
            detail = _nmcli(
                "-f", "802-11-wireless.ssid",
                "connection", "show", name,
                timeout=5,
            )
            if detail.returncode == 0:
                for sl in detail.stdout.split("\n"):
                    if "802-11-wireless.ssid:" in sl:
                        ssid = sl.split(":", 1)[1].strip() or name
                        break
            return {
                "connected": True,
                "ssid": ssid,
                "device": device,
            }
    return {"connected": False, "ssid": None, "device": None}


def connect(ssid: str, password: Optional[str] = None) -> Dict:
    """
    Connect ke SSID. Kalau password None / kosong, asumsikan open network.
    Return: {"ok": bool, "message": str}
    """
    ssid = (ssid or "").strip()
    if not ssid:
        return {"ok": False, "message": "SSID kosong."}

    if password and ("\n" in password or "\r" in password):
        logger.warning("WiFi connect rejected newline in password ssid=%s", ssid)
        return {"ok": False, "message": "Password WiFi mengandung karakter baris baru."}

    logger.info(
        "WiFi connect started ssid=%s secured=%s password_length=%s",
        ssid,
        bool(password),
        len(password) if password else 0,
    )
    started = time.perf_counter()
    try:
        # `--ask` membuat nmcli menerima secret untuk profile baru maupun
        # profile lama yang belum punya PSK. Password dikirim lewat stdin agar
        # tetap bekerja dari systemd tanpa TTY dan tidak terlihat di process list.
        cmd = ["nmcli"]
        input_text = None
        if password:
            cmd.append("--ask")
            input_text = f"{password}\n"
        cmd.extend(["device", "wifi", "connect", ssid])
        result = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "WiFi connect timeout ssid=%s duration_ms=%.1f",
            ssid,
            (time.perf_counter() - started) * 1000,
        )
        return {
            "ok": False,
            "message": "Timeout — mungkin password salah atau signal terlalu lemah.",
        }
    except FileNotFoundError:
        logger.exception("WiFi connect failed: nmcli executable not found")
        return {"ok": False, "message": "nmcli tidak tersedia."}

    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        logger.info(
            "WiFi connect succeeded ssid=%s duration_ms=%.1f",
            ssid,
            (time.perf_counter() - started) * 1000,
        )
        return {"ok": True, "message": output or f"Tersambung ke {ssid}."}

    logger.error(
        "WiFi connect failed ssid=%s code=%s duration_ms=%.1f output=%s",
        ssid,
        result.returncode,
        (time.perf_counter() - started) * 1000,
        output[:1000],
    )
    return {"ok": False, "message": output or "Gagal menyambungkan."}
