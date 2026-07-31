#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QR Code Module untuk Face Recognition System
Generate dan scan QR code dengan enkripsi
"""

import numpy as np
import os
import qrcode
import cv2
from cryptography.fernet import Fernet
from pyzbar.pyzbar import decode
from typing import Optional, Dict
import base64

from backend.core.face_engine import open_camera, ensure_opencv_gui


class QRCodeManager:
    """Manager untuk generate dan scan QR code dengan enkripsi"""

    def __init__(self, db_dir: str = "data/face_db",
                 qr_dir: str = "data/qr_codes"):
        """
        Initialize QR Code Manager

        Args:
            db_dir: Directory database
            qr_dir: Directory untuk menyimpan QR codes
        """
        self.db_dir = db_dir
        self.qr_dir = qr_dir
        self.key_file = os.path.join(db_dir, ".qr_key")

        os.makedirs(qr_dir, exist_ok=True)

        # Load atau generate encryption key
        self.cipher = self._load_or_create_key()

    @staticmethod
    def _safe_path_part(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value))
        return "_".join(part for part in cleaned.split("_") if part) or "UNKNOWN"

    @staticmethod
    def _safe_folder_part(value: str) -> str:
        invalid = '<>:"/\\|?*'
        cleaned = "".join("_" if ch in invalid or ord(ch) < 32 else ch for ch in str(value))
        return cleaned.strip(" .") or "UNKNOWN"

    def _qr_filepath(self, nis: str, kelas: str) -> str:
        class_dir = os.path.join(self.qr_dir, self._safe_folder_part(kelas))
        os.makedirs(class_dir, exist_ok=True)
        return os.path.join(class_dir, f"{self._safe_path_part(nis)}.png")

    def _load_or_create_key(self) -> Fernet:
        """Load encryption key atau buat baru jika belum ada"""
        if os.path.exists(self.key_file):
            with open(self.key_file, 'rb') as f:
                key = f.read()
        else:
            # Generate key baru
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
            print(f"[*] Encryption key created: {self.key_file}")

        # Store key for SHA256 hashing
        self.key = key

        return Fernet(key)

    def encrypt_data(self, data: str) -> str:
        """Encrypt data dan return base64 string"""
        encrypted = self.cipher.encrypt(data.encode())
        return base64.urlsafe_b64encode(encrypted).decode()

    def decrypt_data(self, encrypted_data: str) -> Optional[str]:
        """Decrypt data dari base64 string"""
        try:
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted = self.cipher.decrypt(encrypted_bytes)
            return decrypted.decode()
        except Exception as e:
            print(f"[X] Decryption error: {e}")
            return None

    def generate_qr_for_all(self) -> int:
        """
        Generate QR code untuk semua siswa yang sudah punya parent enrolled.

        Returns:
            Jumlah QR code yang di-generate
        """
        from backend.database.student_db import StudentDatabase

        student_db = StudentDatabase()
        parents = student_db.list_all_parents()
        unique_nis = sorted({parent["nis"] for parent in parents})

        if not unique_nis:
            print("[!] Belum ada parent yang enroll di database MySQL.")
            return 0

        print(f"\n[*] Generating QR codes untuk {len(unique_nis)} siswa...")

        count = 0
        for nis in unique_nis:
            success = self.generate_qr_code(nis)
            if success:
                count += 1

        print(f"\n[OK] {count} QR codes berhasil di-generate!")
        print(f"     Lokasi: {self.qr_dir}/")

        return count

    def generate_qr_code(self, nis: str, silent: bool = False) -> bool:
        """
        Generate QR code untuk satu siswa (1 QR per student)

        Args:
            nis: NIS siswa
            silent: Jika True, tidak print message

        Returns:
            True jika berhasil
        """
        try:
            from backend.database.student_db import StudentDatabase

            student_db = StudentDatabase()
            student = student_db.get_student(nis)
            if not student:
                raise ValueError("NIS tidak ditemukan di database siswa.")

            token = student_db.assign_qr_token(nis)
            qr_content = token

            # Generate QR code dengan setting OPTIMAL untuk kamera low-res
            qr = qrcode.QRCode(
                version=1,  # Versi terkecil (21x21 modules)
                error_correction=qrcode.constants.ERROR_CORRECT_L,  # Low = paling simple
                box_size=30,  # Box besar untuk scan mudah
                border=2,     # Border minimal
            )
            qr.add_data(qr_content)
            qr.make(fit=True)

            # Create image dengan ukuran besar
            img = qr.make_image(fill_color="black", back_color="white")

            filepath = self._qr_filepath(nis, student["kelas"])
            filename = os.path.relpath(filepath, self.qr_dir)
            img.save(filepath)

            old_filepath = os.path.join(self.qr_dir, f"{self._safe_path_part(nis)}.png")
            if os.path.exists(old_filepath) and os.path.abspath(old_filepath) != os.path.abspath(filepath):
                os.remove(old_filepath)

            if not silent:
                print(f"[OK] QR code generated: {filename}")
                print(f"     NIS: {nis}")
                print(f"     Kelas: {student['kelas']}")
                print(f"     Token QR: {token}")

            return True

        except Exception as e:
            if not silent:
                print(f"[X] Error generating QR code for NIS {nis}: {e}")
            return False

    def resolve_qr_data(self, qr_data: str) -> Dict:
        """Resolve random QR token ke data siswa dari MySQL."""
        token = qr_data.strip()
        if not (8 <= len(token) <= 64 and all(ch in "0123456789abcdefABCDEF" for ch in token)):
            return {
                "token": token,
                "nis": None,
                "raw_data": qr_data,
                "verified": False,
            }

        from backend.database.student_db import StudentDatabase

        student = StudentDatabase().get_student_by_qr_token(token)
        if not student:
            return {
                "token": token,
                "nis": None,
                "raw_data": qr_data,
                "verified": False,
            }

        return {
            "token": token,
            "nis": student["nis"],
            "nama": student["nama"],
            "kelas": student["kelas"],
            "raw_data": qr_data,
            "verified": True,
        }

    def scan_qr_from_camera(
            self,
            cam_index: int = 0,
            width: int = 640,
            height: int = 480,
            mirror_camera: bool = False) -> Optional[Dict]:
        """
        Scan QR code dari kamera

        Args:
            cam_index: Index kamera
            width: Lebar frame
            height: Tinggi frame

        Returns:
            Dict dengan info jika berhasil, None jika gagal
        """
        try:
            ensure_opencv_gui()
            cap = open_camera(cam_index, width, height)
        except Exception as e:
            print(f"[X] Tidak bisa membuka camera {cam_index}")
            print(f"    Detail: {e}")
            return None

        print("\n[*] QR CODE SCANNER")
        print("    Arahkan QR code ke kamera")
        print("    Tekan 'q' untuk keluar")
        print("    Kamera akan tetap aktif meskipun QR sudah terdeteksi\n")

        result = None

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if mirror_camera:
                frame = cv2.flip(frame, 1)

            # Decode QR codes
            decoded_objects = decode(frame)

            for obj in decoded_objects:
                # Get QR token data
                qr_data = obj.data.decode('utf-8')

                try:
                    data = self.resolve_qr_data(qr_data)

                    # Draw rectangle around QR code
                    points = obj.polygon
                    if len(points) == 4:
                        pts = [(point.x, point.y) for point in points]
                        cv2.polylines(
                            frame, [
                                np.array(
                                    pts, dtype=np.int32)], True, (0, 255, 0), 3)

                    # Display info
                    cv2.putText(frame, "QR Code Detected!", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                    cv2.putText(frame, f"Token: {data.get('token', '-')}", (10, 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                    result = data

                except Exception as e:
                    cv2.putText(frame, "Invalid QR Code", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            if result is not None:
                status = f"Last QR: {result.get('token', '-')}"
                color = (0, 255, 0) if result.get('verified') else (0, 255, 255)
                cv2.putText(frame, status, (10, height - 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            # Display
            cv2.imshow("QR Code Scanner - Press 'q' to quit", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break

        cap.release()
        cv2.destroyAllWindows()

        return result

    def verify_qr_data(self, data: Dict) -> bool:
        """
        Verify QR code data dengan hash lokal dan database MySQL.

        Args:
            data: Data dari QR code

        Returns:
            True jika valid
        """
        if not data or not data.get("verified") or not data.get("nis"):
            return False

        from backend.database.student_db import StudentDatabase

        student_db = StudentDatabase()
        return student_db.get_student_by_qr_token(data.get("token", "")) is not None


# Import numpy untuk QR detection


if __name__ == "__main__":
    # Test
    qr_manager = QRCodeManager()

    print("QR Code Manager Test")
    print("1. Generate QR codes")
    print("2. Scan QR code")

    choice = input("\nPilih (1/2): ").strip()

    if choice == "1":
        qr_manager.generate_qr_for_all()
    elif choice == "2":
        result = qr_manager.scan_qr_from_camera()
        if result:
            print(f"\n[OK] QR Code scanned successfully!")
            print(f"     NIS: {result['nis']}")
            print(f"     Verified: {result['verified']}")

            if qr_manager.verify_qr_data(result):
                print("     Status: VALID")
            else:
                print("     Status: INVALID")

