"""
Utilities for the local admin dashboard UI.

This module is intentionally placed under backend/ui so dashboard-specific
logic does not have to live entirely inside the FastAPI route file.
"""

import os
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import bcrypt
import mysql.connector


def _env(name: str, legacy_name: str, default: str) -> str:
    return os.getenv(name, os.getenv(legacy_name, default))


class AdminAccountStore:
    """Read dashboard accounts from the main SafePick MySQL database."""

    def __init__(self):
        self.host = _env("SAFEPICK_DB_HOST", "FACEGATE_DB_HOST", "127.0.0.1")
        self.port = int(_env("SAFEPICK_DB_PORT", "FACEGATE_DB_PORT", "3306"))
        self.user = _env("SAFEPICK_DB_USER", "FACEGATE_DB_USER", "root")
        self.password = _env("SAFEPICK_DB_PASSWORD", "FACEGATE_DB_PASSWORD", "")
        self.database = _env("SAFEPICK_DB_NAME", "FACEGATE_DB_NAME", "safepick")
        self.legacy_database = _env("SAFEPICK_LEGACY_ADMIN_DB_NAME", "FACEGATE_LEGACY_ADMIN_DB_NAME", "admin")
        self._ensure_ready()

    def _server_config(self) -> Dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "charset": "utf8mb4",
            "collation": "utf8mb4_unicode_ci",
        }

    def _config(self) -> Dict:
        config = self._server_config()
        config["database"] = self.database
        return config

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return f"`{value.replace('`', '``')}`"

    def _ensure_ready(self) -> None:
        with closing(mysql.connector.connect(**self._server_config())) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS {self._quote_identifier(self.database)} "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            conn.commit()

        with closing(mysql.connector.connect(**self._config())) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS account (
                        username VARCHAR(255) PRIMARY KEY,
                        password_hash VARCHAR(255) NULL
                    ) ENGINE=InnoDB
                """)
                if not self._column_exists(cursor, self.database, "account", "password_hash"):
                    cursor.execute(
                        "ALTER TABLE account ADD COLUMN password_hash VARCHAR(255) NULL"
                    )
                self._migrate_plaintext_passwords(cursor)
                if self._column_exists(cursor, self.database, "account", "password"):
                    cursor.execute("ALTER TABLE account DROP COLUMN password")
            conn.commit()

        self._migrate_legacy_accounts()

    def _database_exists(self, cursor, database: str) -> bool:
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.schemata
            WHERE schema_name = %s
        """, (database,))
        return cursor.fetchone()[0] > 0

    def _table_exists(self, cursor, database: str, table: str) -> bool:
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name = %s
        """, (database, table))
        return cursor.fetchone()[0] > 0

    def _column_exists(self, cursor, database: str, table: str, column: str) -> bool:
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
        """, (database, table, column))
        return cursor.fetchone()[0] > 0

    def _migrate_plaintext_passwords(self, cursor) -> None:
        """Hash legacy plaintext passwords before dropping the old column."""
        if not self._column_exists(cursor, self.database, "account", "password"):
            return

        cursor.execute("""
            SELECT username, password
            FROM account
            WHERE password IS NOT NULL
              AND password <> ''
              AND (password_hash IS NULL OR password_hash = '')
        """)
        for username, plaintext in cursor.fetchall():
            cursor.execute(
                "UPDATE account SET password_hash = %s WHERE username = %s",
                (self._hash_password(str(plaintext)), username),
            )

    def _migrate_legacy_accounts(self) -> None:
        if not self.legacy_database or self.legacy_database == self.database:
            return

        with closing(mysql.connector.connect(**self._server_config())) as conn:
            with closing(conn.cursor()) as cursor:
                if not self._database_exists(cursor, self.legacy_database):
                    return
                if not self._table_exists(cursor, self.legacy_database, "account"):
                    return
                if not self._column_exists(cursor, self.legacy_database, "account", "password"):
                    return

                legacy_db = self._quote_identifier(self.legacy_database)
                cursor.execute(f"SELECT username, password FROM {legacy_db}.account")
                rows = cursor.fetchall()
                for username, plaintext in rows:
                    if plaintext is None:
                        continue
                    hashed = self._hash_password(str(plaintext))
                    cursor.execute(
                        f"""
                        INSERT INTO {self._quote_identifier(self.database)}.account
                            (username, password_hash)
                        VALUES (%s, %s)
                        ON DUPLICATE KEY UPDATE
                            password_hash = COALESCE(password_hash, VALUES(password_hash))
                        """,
                        (username, hashed),
                    )
            conn.commit()

    @staticmethod
    def _hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def verify(self, username: str, password: str) -> bool:
        username = username.strip()
        if not username or not password:
            return False

        with closing(mysql.connector.connect(**self._config())) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    "SELECT password_hash FROM account WHERE username = %s LIMIT 1",
                    (username,),
                )
                row = cursor.fetchone()

        if not row:
            return False

        hashed_db = row[0]

        if hashed_db:
            try:
                return bcrypt.checkpw(
                    password.encode("utf-8"),
                    str(hashed_db).encode("utf-8"),
                )
            except (ValueError, TypeError):
                return False

        return False

    def account_exists(self, username: str) -> bool:
        """Return True when an admin account already exists."""
        username = username.strip()
        if not username:
            return False

        with closing(mysql.connector.connect(**self._config())) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    "SELECT 1 FROM account WHERE username = %s LIMIT 1",
                    (username,),
                )
                return cursor.fetchone() is not None

    def create_or_update_account(self, username: str, password: str) -> None:
        """Insert or replace an account, always storing as bcrypt hash."""
        username = username.strip()
        if not username or not password:
            raise ValueError("username dan password wajib diisi.")

        hashed = self._hash_password(password)
        with closing(mysql.connector.connect(**self._config())) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    "INSERT INTO account (username, password_hash) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE password_hash = VALUES(password_hash)",
                    (username, hashed),
                )
            conn.commit()

    def rename_account(self, old_username: str, new_username: str, new_password: str) -> None:
        """Change an account username and password in one operation."""
        old_username = old_username.strip()
        new_username = new_username.strip()
        if not old_username or not new_username or not new_password:
            raise ValueError("username lama, username baru, dan password baru wajib diisi.")

        hashed = self._hash_password(new_password)
        with closing(mysql.connector.connect(**self._config())) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    "UPDATE account SET username = %s, password_hash = %s WHERE username = %s",
                    (new_username, hashed, old_username),
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"Akun '{old_username}' tidak ditemukan.")
            conn.commit()


def evidence_url(photo_path: Optional[str], project_root: Path) -> Optional[str]:
    """Return a safe URL for a stored evidence photo when it is inside the repo."""

    if not photo_path:
        return None

    raw_path = Path(photo_path)
    if not raw_path.is_absolute():
        raw_path = project_root / raw_path

    try:
        resolved = raw_path.resolve()
        project_root = project_root.resolve()
        resolved.relative_to(project_root)
    except ValueError:
        return None

    return f"/api/logs/evidence/{resolved.relative_to(project_root).as_posix()}"


def serialize_log_rows(rows: List[Dict], project_root: Path) -> List[Dict]:
    result = []
    for row in rows:
        logged_at = row.get("waktu_absen")
        if isinstance(logged_at, datetime):
            logged_at = logged_at.strftime("%Y-%m-%d %H:%M:%S")
        cancelled_at = row.get("cancelled_at")
        if isinstance(cancelled_at, datetime):
            cancelled_at = cancelled_at.strftime("%Y-%m-%d %H:%M:%S")

        result.append(
            {
                "id": row.get("id"),
                "nis": row.get("nis"),
                "nama_siswa": row.get("nama_siswa"),
                "kelas": row.get("kelas"),
                "jenis_absen": row.get("jenis_absen"),
                "waktu_absen": logged_at,
                "bukti_foto": evidence_url(row.get("bukti_foto"), project_root),
                "status": row.get("status") or "AKTIF",
                "cancel_reason": row.get("cancel_reason"),
                "cancelled_at": cancelled_at,
            }
        )
    return result
