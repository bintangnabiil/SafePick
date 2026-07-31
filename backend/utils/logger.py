#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Logging module untuk SafePick.
Menyediakan logging untuk enrollment, recognition, dan system events
"""

import os
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"


def configure_app_logging(log_dir: Optional[str] = None) -> logging.Logger:
    """Configure rotating diagnostic log used by web app and hardware helpers."""
    logger = logging.getLogger("safepick.app")
    if getattr(logger, "_safepick_configured", False):
        return logger

    level_name = os.getenv("SAFEPICK_LOG_LEVEL", os.getenv("FACEGATE_LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    target_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | pid=%(process)d | "
        "%(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        target_dir / "app.log",
        maxBytes=int(os.getenv("SAFEPICK_LOG_MAX_BYTES", os.getenv("FACEGATE_LOG_MAX_BYTES", str(5 * 1024 * 1024)))),
        backupCount=int(os.getenv("SAFEPICK_LOG_BACKUP_COUNT", os.getenv("FACEGATE_LOG_BACKUP_COUNT", "5"))),
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    logger.setLevel(level)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    logger._safepick_configured = True
    return logger


def get_app_logger() -> logging.Logger:
    return configure_app_logging()


class FaceRecognitionLogger:
    """Logger untuk Face Recognition System"""

    def __init__(self, log_dir: str = str(DEFAULT_LOG_DIR)):
        """
        Initialize logger

        Args:
            log_dir: Directory untuk menyimpan log files
        """
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        # Setup loggers
        self.system_logger = self._setup_logger("system", "system.log")
        self.enrollment_logger = self._setup_logger(
            "enrollment", "enrollment.log")
        self.recognition_logger = self._setup_logger(
            "recognition", "recognition.log")
        self.access_logger = self._setup_logger("access", "access.log")

    def _setup_logger(self, name: str, filename: str) -> logging.Logger:
        """Setup individual logger dengan file handler"""
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)

        # Hindari duplicate handlers
        if logger.handlers:
            return logger

        # File handler
        log_path = os.path.join(self.log_dir, filename)
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO)

        # Console handler (optional)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)

        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger

    def log_system(self, message: str, level: str = "INFO"):
        """Log system events"""
        if level == "INFO":
            self.system_logger.info(message)
        elif level == "WARNING":
            self.system_logger.warning(message)
        elif level == "ERROR":
            self.system_logger.error(message)
        elif level == "DEBUG":
            self.system_logger.debug(message)

    def log_enrollment(self, name: str, samples: int, success: bool = True,
                       camera_index: int = 0, notes: str = ""):
        """
        Log enrollment event

        Args:
            name: Nama orang yang di-enroll
            samples: Jumlah samples yang diambil
            success: Apakah enrollment berhasil
            camera_index: Index kamera yang digunakan
            notes: Catatan tambahan
        """
        status = "SUCCESS" if success else "FAILED"
        message = f"ENROLL | {status} | Name: {name} | Samples: {samples} | Camera: {camera_index}"
        if notes:
            message += f" | Notes: {notes}"

        self.enrollment_logger.info(message)

    def log_recognition(self, detected_name: Optional[str], similarity: float,
                        camera_index: int = 0, threshold: float = 0.35):
        """
        Log recognition event

        Args:
            detected_name: Nama yang terdeteksi (None jika unknown)
            similarity: Similarity score
            camera_index: Index kamera
            threshold: Threshold yang digunakan
        """
        if detected_name:
            status = "RECOGNIZED"
            message = f"RECOGNIZE | {status} | Name: {detected_name} | Similarity: {similarity:.3f} | Threshold: {threshold} | Camera: {camera_index}"
        else:
            status = "UNKNOWN"
            message = f"RECOGNIZE | {status} | Similarity: {similarity:.3f} | Threshold: {threshold} | Camera: {camera_index}"

        self.recognition_logger.info(message)

    def log_access(self, name: str, granted: bool = True, reason: str = ""):
        """
        Log access control event

        Args:
            name: Nama orang
            granted: Apakah akses diberikan
            reason: Alasan (jika ditolak)
        """
        status = "GRANTED" if granted else "DENIED"
        message = f"ACCESS | {status} | Name: {name}"
        if reason:
            message += f" | Reason: {reason}"

        self.access_logger.info(message)

    def log_camera_switch(self, old_index: int, new_index: int):
        """Log camera switch event"""
        message = f"CAMERA_SWITCH | From: {old_index} | To: {new_index}"
        self.system_logger.info(message)

    def log_database_reset(self, user: str = "system"):
        """Log database reset event"""
        message = f"DATABASE_RESET | User: {user}"
        self.system_logger.warning(message)

    def log_model_load(
            self,
            model_name: str,
            device: str,
            success: bool = True):
        """Log model loading event"""
        status = "SUCCESS" if success else "FAILED"
        message = f"MODEL_LOAD | {status} | Model: {model_name} | Device: {device}"
        self.system_logger.info(message)

    def log_error(
            self,
            error_type: str,
            error_message: str,
            context: str = ""):
        """Log error event"""
        message = f"ERROR | Type: {error_type} | Message: {error_message}"
        if context:
            message += f" | Context: {context}"
        self.system_logger.error(message)

    def get_enrollment_stats(self) -> dict:
        """Get enrollment statistics dari log file"""
        stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "names": []
        }

        log_path = os.path.join(self.log_dir, "enrollment.log")
        if not os.path.exists(log_path):
            return stats

        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                if "ENROLL" in line:
                    stats["total"] += 1
                    if "SUCCESS" in line:
                        stats["success"] += 1
                        # Extract name
                        if "Name:" in line:
                            name = line.split("Name:")[1].split("|")[0].strip()
                            if name not in stats["names"]:
                                stats["names"].append(name)
                    elif "FAILED" in line:
                        stats["failed"] += 1

        return stats

    def get_recognition_stats(self, hours: int = 24) -> dict:
        """Get recognition statistics untuk N jam terakhir"""
        stats = {
            "total": 0,
            "recognized": 0,
            "unknown": 0,
            "unique_faces": set()
        }

        log_path = os.path.join(self.log_dir, "recognition.log")
        if not os.path.exists(log_path):
            return {**stats, "unique_faces": []}

        cutoff_time = datetime.now().timestamp() - (hours * 3600)

        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                if "RECOGNIZE" in line:
                    # Parse timestamp
                    try:
                        timestamp_str = line.split("|")[0].strip()
                        log_time = datetime.strptime(
                            timestamp_str, "%Y-%m-%d %H:%M:%S")

                        if log_time.timestamp() < cutoff_time:
                            continue

                        stats["total"] += 1

                        if "RECOGNIZED" in line:
                            stats["recognized"] += 1
                            if "Name:" in line:
                                name = line.split("Name:")[1].split("|")[
                                    0].strip()
                                stats["unique_faces"].add(name)
                        elif "UNKNOWN" in line:
                            stats["unknown"] += 1
                    except BaseException:
                        continue

        return {**stats, "unique_faces": list(stats["unique_faces"])}


# Singleton instance
_logger_instance = None


def get_logger() -> FaceRecognitionLogger:
    """Get singleton logger instance"""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = FaceRecognitionLogger()
    return _logger_instance
