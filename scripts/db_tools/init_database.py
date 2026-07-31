"""Initialize the SafePick MySQL database for a fresh installation."""

import os
import sys

sys.path.append(
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            '../../')))

from backend.database.student_db import StudentDatabase
from backend.ui.dashboard import AdminAccountStore


def main():
    print("Initializing SafePick database...")

    db = StudentDatabase()
    account_store = AdminAccountStore()

    existing_students = db.list_all_students()
    print("[OK] Database and core tables are ready.")
    print(f"     Students registered: {len(existing_students)}")

    if not account_store.account_exists("admin"):
        account_store.create_or_update_account("admin", "admin123")
        print("[OK] Default admin account created.")
    else:
        print("[OK] Admin account already exists; password was not changed.")

    print("")
    print("Default login:")
    print("  Username : admin")
    print("  Password : admin123")
    print("")
    print("Please change the password before the system is used in production.")


if __name__ == "__main__":
    main()
