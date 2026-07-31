"""
Database Manager untuk SafePick.
Menggunakan MySQL untuk data siswa dan orang tua
"""

import os
import secrets
from contextlib import closing
from typing import List, Tuple, Optional, Dict

try:
    import pymysql
    pymysql.install_as_MySQLdb()
    _connector = pymysql
    _Error = pymysql.Error
except ImportError:
    import mysql.connector
    _connector = mysql.connector
    _Error = mysql.connector.Error


def _env(name: str, legacy_name: str, default: str) -> str:
    return os.getenv(name, os.getenv(legacy_name, default))


class StudentDatabase:
    """Manage student database (NIS, Nama, Kelas)"""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize student database.

        Args:
            db_path: Tidak digunakan lagi. Dipertahankan agar call site lama tidak langsung rusak.
        """
        self.host = _env("SAFEPICK_DB_HOST", "FACEGATE_DB_HOST", "127.0.0.1")
        self.port = int(_env("SAFEPICK_DB_PORT", "FACEGATE_DB_PORT", "3306"))
        self.user = _env("SAFEPICK_DB_USER", "FACEGATE_DB_USER", "root")
        self.password = _env("SAFEPICK_DB_PASSWORD", "FACEGATE_DB_PASSWORD", "")
        self.database = _env("SAFEPICK_DB_NAME", "FACEGATE_DB_NAME", "safepick")
        self.db_path = db_path
        self._ensure_database_exists()
        self._init_database()

    def _server_config(self) -> Dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "charset": "utf8mb4",
            "collation": "utf8mb4_unicode_ci",
        }

    def _database_config(self) -> Dict:
        config = self._server_config().copy()
        config["database"] = self.database
        return config

    def _connect_server(self):
        return _connector.connect(**self._server_config())

    def _connect_database(self):
        return _connector.connect(**self._database_config())

    def _ensure_database_exists(self):
        with closing(self._connect_server()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            conn.commit()

    def _init_database(self):
        """Create tables if not exist"""
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS students (
                        nis VARCHAR(32) PRIMARY KEY,
                        nama VARCHAR(255) NOT NULL,
                        kelas VARCHAR(64) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS parents (
                        id INT PRIMARY KEY AUTO_INCREMENT,
                        nis VARCHAR(32) NOT NULL,
                        nama_ortu VARCHAR(255) NOT NULL,
                        embedding_index INT NOT NULL,
                        enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_parents_embedding_index (embedding_index),
                        CONSTRAINT fk_parents_students_nis
                            FOREIGN KEY (nis) REFERENCES students(nis)
                            ON DELETE RESTRICT
                            ON UPDATE CASCADE
                    ) ENGINE=InnoDB
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS attendance_logs (
                        id INT PRIMARY KEY AUTO_INCREMENT,
                        nis VARCHAR(32) NULL,
                        kelas VARCHAR(64) NULL,
                        jenis_absen VARCHAR(64) NOT NULL,
                        bukti_foto VARCHAR(512) NOT NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'AKTIF',
                        cancel_reason VARCHAR(255) NULL,
                        cancelled_at TIMESTAMP NULL,
                        waktu_absen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_attendance_logs_waktu (waktu_absen),
                        INDEX idx_attendance_logs_nis (nis),
                        INDEX idx_attendance_logs_status (status)
                    ) ENGINE=InnoDB
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS qr_tokens (
                        token VARCHAR(64) PRIMARY KEY,
                        nis VARCHAR(32) NOT NULL,
                        is_active TINYINT(1) NOT NULL DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_qr_tokens_nis (nis),
                        CONSTRAINT fk_qr_tokens_students_nis
                            FOREIGN KEY (nis) REFERENCES students(nis)
                            ON DELETE RESTRICT
                            ON UPDATE CASCADE
                    ) ENGINE=InnoDB
                """)

                self._ensure_index(
                    cursor, "parents", "idx_parents_nis", "nis")
                self._ensure_unique_index(
                    cursor, "parents", "uq_parents_embedding_index", "embedding_index")
                self._ensure_attendance_log_columns(cursor)
                self._ensure_index(
                    cursor, "attendance_logs", "idx_attendance_logs_waktu", "waktu_absen")
                self._ensure_index(
                    cursor, "attendance_logs", "idx_attendance_logs_nis", "nis")
                self._ensure_index(
                    cursor, "attendance_logs", "idx_attendance_logs_status", "status")
                self._ensure_unique_index(
                    cursor, "qr_tokens", "uq_qr_tokens_nis", "nis")
            conn.commit()

    def _ensure_column(self, cursor, table_name: str, column_name: str, ddl: str):
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
        """, (self.database, table_name, column_name))

        exists = cursor.fetchone()[0] > 0
        if not exists:
            cursor.execute(f"ALTER TABLE `{table_name}` ADD COLUMN {ddl}")

    def _ensure_varchar_width(self, cursor, table_name: str, column_name: str, ddl: str):
        cursor.execute("""
            SELECT CHARACTER_MAXIMUM_LENGTH
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
        """, (self.database, table_name, column_name))
        row = cursor.fetchone()
        if row and row[0] is not None and int(row[0]) < 64:
            cursor.execute(f"ALTER TABLE `{table_name}` MODIFY COLUMN {ddl}")

    def _ensure_nullable(self, cursor, table_name: str, column_name: str, ddl: str):
        """MODIFY kolom jadi NULL kalau schema lama masih NOT NULL.

        Dipakai untuk attendance_logs.nis: log UNKNOWN_FACE insert nis=NULL.
        DB lama (dibuat sebelum nis di-NULL-kan) bikin insert gagal -> foto
        tersimpan di disk tapi log tidak masuk DB.
        """
        cursor.execute("""
            SELECT IS_NULLABLE
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
        """, (self.database, table_name, column_name))
        row = cursor.fetchone()
        if row and str(row[0]).upper() == "NO":
            cursor.execute(f"ALTER TABLE `{table_name}` MODIFY COLUMN {ddl}")

    def _ensure_attendance_log_columns(self, cursor):
        self._ensure_column(cursor, "attendance_logs", "kelas", "kelas VARCHAR(64) NULL")
        self._ensure_column(cursor, "attendance_logs", "bukti_foto", "bukti_foto VARCHAR(512) NULL")
        self._ensure_column(
            cursor,
            "attendance_logs",
            "status",
            "status VARCHAR(32) NOT NULL DEFAULT 'AKTIF'",
        )
        self._ensure_column(cursor, "attendance_logs", "cancel_reason", "cancel_reason VARCHAR(255) NULL")
        self._ensure_column(cursor, "attendance_logs", "cancelled_at", "cancelled_at TIMESTAMP NULL")
        # UNKNOWN_FACE log insert nis=NULL. DB lama pakai nis NOT NULL -> migrate.
        self._ensure_nullable(cursor, "attendance_logs", "nis", "nis VARCHAR(32) NULL")
        self._ensure_varchar_width(cursor, "qr_tokens", "token", "token VARCHAR(64) NOT NULL")

    def _ensure_index(self, cursor, table_name: str, index_name: str, column_name: str):
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema = %s
              AND table_name = %s
              AND index_name = %s
        """, (self.database, table_name, index_name))

        exists = cursor.fetchone()[0] > 0
        if not exists:
            cursor.execute(
                f"CREATE INDEX `{index_name}` ON `{table_name}`(`{column_name}`)")

    def _ensure_unique_index(self, cursor, table_name: str, index_name: str, column_name: str):
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema = %s
              AND table_name = %s
              AND index_name = %s
        """, (self.database, table_name, index_name))

        exists = cursor.fetchone()[0] > 0
        if exists:
            return

        cursor.execute(f"""
            SELECT `{column_name}`, COUNT(*)
            FROM `{table_name}`
            GROUP BY `{column_name}`
            HAVING COUNT(*) > 1
            LIMIT 1
        """)
        if cursor.fetchone():
            return

        cursor.execute(
            f"CREATE UNIQUE INDEX `{index_name}` ON `{table_name}`(`{column_name}`)")

    def add_student(self, nis: str, nama: str, kelas: str) -> bool:
        try:
            with closing(self._connect_database()) as conn:
                with closing(conn.cursor()) as cursor:
                    cursor.execute("""
                        INSERT INTO students (nis, nama, kelas)
                        VALUES (%s, %s, %s)
                    """, (nis, nama, kelas))
                conn.commit()
            return True
        except _Error as e:
            if getattr(e, "errno", None) == 1062:
                return False
            raise

    def update_student(self, nis: str, nama: str, kelas: str) -> bool:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    UPDATE students
                    SET nama = %s, kelas = %s
                    WHERE nis = %s
                """, (nama, kelas, nis))
                updated = cursor.rowcount
                if updated == 0:
                    cursor.execute("SELECT COUNT(*) FROM students WHERE nis = %s", (nis,))
                    updated = cursor.fetchone()[0]
            conn.commit()

        return updated > 0

    def delete_student(self, nis: str) -> bool:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                # QR token dibuat otomatis (bukan data yang perlu dijaga), jadi
                # bersihkan dulu supaya tidak ikut memblok penghapusan siswa.
                # Parent SENGAJA tidak disentuh: FK parents.nis (ON DELETE
                # RESTRICT) akan menggagalkan DELETE di bawah bila siswa masih
                # punya parent -> caller memunculkan pesan "reset parent dulu".
                # Kalau DELETE students gagal karena FK, commit tidak tercapai
                # sehingga penghapusan qr_tokens ikut ter-rollback (atomik).
                cursor.execute("DELETE FROM qr_tokens WHERE nis = %s", (nis,))
                cursor.execute("DELETE FROM students WHERE nis = %s", (nis,))
                deleted = cursor.rowcount
            conn.commit()

        return deleted > 0

    def get_student(self, nis: str) -> Optional[Dict[str, str]]:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT nis, nama, kelas, created_at
                    FROM students
                    WHERE nis = %s
                """, (nis,))
                row = cursor.fetchone()

        if row:
            return {
                "nis": row[0],
                "nama": row[1],
                "kelas": row[2],
                "created_at": row[3],
            }
        return None

    def add_parent(self, nis: str, nama_ortu: str, embedding_index: int) -> bool:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    INSERT INTO parents (nis, nama_ortu, embedding_index)
                    VALUES (%s, %s, %s)
                """, (nis, nama_ortu, embedding_index))
            conn.commit()
        return True

    def update_parent(self, parent_id: int, nis: str, nama_ortu: str) -> bool:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    UPDATE parents
                    SET nis = %s, nama_ortu = %s
                    WHERE id = %s
                """, (nis, nama_ortu, parent_id))
                updated = cursor.rowcount
                if updated == 0:
                    cursor.execute("SELECT COUNT(*) FROM parents WHERE id = %s", (parent_id,))
                    updated = cursor.fetchone()[0]
            conn.commit()

        return updated > 0

    def delete_parent(self, parent_id: int) -> Optional[int]:
        """
        Hapus parent dan kembalikan embedding_index baris yang dihapus.
        Return None kalau parent tidak ditemukan. Caller wajib men-tombstone
        embedding_index yang dikembalikan agar tidak jadi ghost embedding.
        """
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    "SELECT embedding_index FROM parents WHERE id = %s",
                    (parent_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                embedding_index = int(row[0])

                cursor.execute("DELETE FROM parents WHERE id = %s", (parent_id,))
            conn.commit()

        return embedding_index

    def get_parent_by_index(self, embedding_index: int) -> Optional[Dict]:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT p.id, p.nis, p.nama_ortu, p.embedding_index,
                           s.nama, s.kelas
                    FROM parents p
                    JOIN students s ON p.nis = s.nis
                    WHERE p.embedding_index = %s
                """, (embedding_index,))
                row = cursor.fetchone()

        if row:
            return {
                "parent_id": row[0],
                "nis": row[1],
                "nama_ortu": row[2],
                "embedding_index": row[3],
                "nama_anak": row[4],
                "kelas": row[5],
            }
        return None

    def get_parent_by_nis(self, nis: str) -> Optional[Dict]:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT p.id, p.nis, p.nama_ortu, p.embedding_index,
                           s.nama, s.kelas
                    FROM parents p
                    JOIN students s ON p.nis = s.nis
                    WHERE p.nis = %s
                """, (nis,))
                row = cursor.fetchone()

        if row:
            return {
                "parent_id": row[0],
                "nis": row[1],
                "nama_ortu": row[2],
                "embedding_index": row[3],
                "nama_anak": row[4],
                "kelas": row[5],
            }
        return None

    def search_parents_by_child(self, child_name: str) -> List[Dict]:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT p.id, p.nis, p.nama_ortu, p.embedding_index,
                           s.nama, s.kelas
                    FROM parents p
                    JOIN students s ON p.nis = s.nis
                    WHERE LOWER(s.nama) LIKE LOWER(%s)
                    ORDER BY s.kelas, s.nama, p.nama_ortu
                """, (f"%{child_name}%",))
                rows = cursor.fetchall()

        return [{
            "parent_id": r[0],
            "nis": r[1],
            "nama_ortu": r[2],
            "embedding_index": r[3],
            "nama_anak": r[4],
            "kelas": r[5],
        } for r in rows]

    def search_parents_by_class(self, class_name: str) -> List[Dict]:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT p.id, p.nis, p.nama_ortu, p.embedding_index,
                           s.nama, s.kelas
                    FROM parents p
                    JOIN students s ON p.nis = s.nis
                    WHERE LOWER(s.kelas) LIKE LOWER(%s)
                    ORDER BY s.kelas, s.nama, p.nama_ortu
                """, (f"%{class_name}%",))
                rows = cursor.fetchall()

        return [{
            "parent_id": r[0],
            "nis": r[1],
            "nama_ortu": r[2],
            "embedding_index": r[3],
            "nama_anak": r[4],
            "kelas": r[5],
        } for r in rows]

    def list_all_students(self) -> List[Dict]:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT nis, nama, kelas FROM students ORDER BY kelas, nama")
                rows = cursor.fetchall()

        return [{"nis": r[0], "nama": r[1], "kelas": r[2]} for r in rows]

    def list_all_parents(self) -> List[Dict]:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT p.id, p.nis, p.nama_ortu, p.embedding_index, s.nama, s.kelas
                    FROM parents p
                    JOIN students s ON p.nis = s.nis
                    ORDER BY s.kelas, s.nama, p.nama_ortu
                """)
                rows = cursor.fetchall()

        return [{
            "parent_id": r[0],
            "nis": r[1],
            "nama_ortu": r[2],
            "embedding_index": r[3],
            "nama_anak": r[4],
            "kelas": r[5],
        } for r in rows]

    def bulk_reassign_embedding_indices(self, assignments: List[Tuple[int, int]]) -> int:
        """
        Atomic bulk-reassign parents.embedding_index ke posisi baru.
        Dipakai oleh vacuum: setelah `embeddings.npy` di-rebuild dan baris
        bergeser, DB harus diperbarui agar mapping tetap konsisten.

        Constraint UNIQUE pada embedding_index dicek InnoDB segera, bukan saat
        commit, jadi UPDATE langsung ke nilai final akan gagal kalau target
        bertabrakan dengan baris lain yang belum dipindah. Solusinya: dua pass
        via "parking" ke nilai negatif (pakai -parent_id yang dijamin unik),
        baru set ke nilai final.

        Args:
            assignments: [(parent_id, new_embedding_index), ...]

        Returns:
            Jumlah baris yang dipindah (yang nilainya benar-benar berubah).
        """
        if not assignments:
            return 0

        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                # Pass 1: parkir ke nilai negatif (-parent_id) supaya bebas kolisi
                cursor.executemany(
                    "UPDATE parents SET embedding_index = %s WHERE id = %s",
                    [(-pid, pid) for pid, _ in assignments])
                # Pass 2: set ke nilai final
                cursor.executemany(
                    "UPDATE parents SET embedding_index = %s WHERE id = %s",
                    [(new_idx, pid) for pid, new_idx in assignments])
                affected = cursor.rowcount
            conn.commit()

        return affected

    def get_summary(self) -> Dict[str, int]:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT COUNT(*) FROM students")
                total_students = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM parents")
                total_parents = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(DISTINCT nis) FROM parents")
                enrolled_students = cursor.fetchone()[0]

        return {
            "total_students": total_students,
            "total_parents": total_parents,
            "enrolled_students": enrolled_students,
            "unenrolled_students": max(0, total_students - enrolled_students),
        }

    def clear_parents(self) -> int:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT COUNT(*) FROM parents")
                deleted = cursor.fetchone()[0]
                cursor.execute("DELETE FROM parents")
                cursor.execute("ALTER TABLE parents AUTO_INCREMENT = 1")
            conn.commit()

        return deleted

    def add_attendance_log(self, nis: Optional[str], jenis_absen: str, bukti_foto: str) -> int:
        kelas = None
        if nis:
            student = self.get_student(nis)
            kelas = student["kelas"] if student else None

        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    INSERT INTO attendance_logs (nis, kelas, jenis_absen, bukti_foto)
                    VALUES (%s, %s, %s, %s)
                """, (nis, kelas, jenis_absen, bukti_foto))
                log_id = cursor.lastrowid
            conn.commit()

        return int(log_id)

    def list_attendance_logs(self, limit: int = 100) -> List[Dict]:
        limit = max(1, min(int(limit), 500))
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(f"""
                    SELECT l.id, l.nis, COALESCE(l.kelas, s.kelas) AS kelas,
                           s.nama AS nama_siswa, l.jenis_absen, l.waktu_absen,
                           l.bukti_foto, l.status, l.cancel_reason, l.cancelled_at
                    FROM attendance_logs l
                    LEFT JOIN students s ON l.nis = s.nis
                    ORDER BY l.waktu_absen DESC, l.id DESC
                    LIMIT {limit}
                """)
                rows = cursor.fetchall()

        return [{
            "id": r[0],
            "nis": r[1],
            "kelas": r[2],
            "nama_siswa": r[3],
            "jenis_absen": r[4],
            "waktu_absen": r[5],
            "bukti_foto": r[6],
            "status": r[7],
            "cancel_reason": r[8],
            "cancelled_at": r[9],
        } for r in rows]

    def cancel_attendance_log(self, log_id: int, reason: str) -> bool:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    UPDATE attendance_logs
                    SET status = 'DIBATALKAN', cancel_reason = %s, cancelled_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status = 'AKTIF'
                """, (reason, log_id))
                updated = cursor.rowcount
            conn.commit()

        return updated > 0

    def _generate_qr_token_candidate(self) -> str:
        return secrets.token_hex(8)

    def assign_qr_token(self, nis: str) -> str:
        nis = nis.strip()
        if not self.get_student(nis):
            raise ValueError("NIS tidak ditemukan di database siswa.")

        existing = self.get_qr_token_by_nis(nis)
        if existing and len(existing["token"]) == 16:
            return existing["token"]

        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                for _ in range(100):
                    token = self._generate_qr_token_candidate()
                    try:
                        if existing:
                            cursor.execute("""
                                UPDATE qr_tokens
                                SET token = %s, is_active = 1
                                WHERE nis = %s
                            """, (token, nis))
                        else:
                            cursor.execute("""
                                INSERT INTO qr_tokens (token, nis, is_active)
                                VALUES (%s, %s, 1)
                            """, (token, nis))
                        conn.commit()
                        return token
                    except _Error as exc:
                        errno = getattr(exc, "errno", None)
                        if errno is None and getattr(exc, "args", None):
                            errno = exc.args[0]
                        if errno == 1062:
                            continue
                        raise

        raise RuntimeError("Gagal membuat token QR unik.")

    def get_qr_token_by_nis(self, nis: str) -> Optional[Dict]:
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT token, nis, is_active, created_at, updated_at
                    FROM qr_tokens
                    WHERE nis = %s AND is_active = 1
                    LIMIT 1
                """, (nis.strip(),))
                row = cursor.fetchone()

        if not row:
            return None
        return {
            "token": row[0],
            "nis": row[1],
            "is_active": bool(row[2]),
            "created_at": row[3],
            "updated_at": row[4],
        }

    def get_student_by_qr_token(self, token: str) -> Optional[Dict]:
        token = token.strip()
        with closing(self._connect_database()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT q.token, s.nis, s.nama, s.kelas
                    FROM qr_tokens q
                    JOIN students s ON q.nis = s.nis
                    WHERE q.token = %s AND q.is_active = 1
                    LIMIT 1
                """, (token,))
                row = cursor.fetchone()

        if not row:
            return None
        return {
            "token": row[0],
            "nis": row[1],
            "nama": row[2],
            "kelas": row[3],
        }

    def import_students_from_csv(self, csv_file: str) -> Tuple[int, int]:
        import csv

        success = 0
        errors = 0

        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader)

            for row in reader:
                if len(row) >= 3:
                    nis, nama, kelas = row[0].strip(), row[1].strip(), row[2].strip()
                    if self.add_student(nis, nama, kelas):
                        success += 1
                    else:
                        errors += 1

        return success, errors

    def export_students_to_csv(self, csv_file: str) -> int:
        import csv

        students = self.list_all_students()

        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["NIS", "Nama", "Kelas"])

            for s in students:
                writer.writerow([s["nis"], s["nama"], s["kelas"]])

        return len(students)


if __name__ == "__main__":
    db = StudentDatabase()
    print("Adding sample students...")
    db.add_student("2024001", "Andi Pratama", "3A")
    db.add_student("2024002", "Rina Sari", "2B")
    db.add_student("2024003", "Riko Saputra", "1C")
    print("\nGet student by NIS:")
    print(db.get_student("2024001"))
    print("\nAdd parent enrollment:")
    db.add_parent("2024001", "Budi Santoso", 0)
    print("\nGet parent by embedding index:")
    print(db.get_parent_by_index(0))
    print("\nAll students:")
    for s in db.list_all_students():
        print(f"  {s['nis']} - {s['nama']} ({s['kelas']})")
