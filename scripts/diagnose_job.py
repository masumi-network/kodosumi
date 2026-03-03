#!/usr/bin/env python3
"""
Diagnose job issues on the cluster.
"""

import sqlite3
import sys
from pathlib import Path
from pprint import pprint

# Default exec dir
EXEC_DIR = Path("./data/execution")
DB_FILE = "sqlite3.db"


def find_job(job_id: str) -> Path | None:
    """Find job database across all user directories."""
    for user_dir in EXEC_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        db_path = user_dir / job_id / DB_FILE
        if db_path.exists():
            return db_path
    return None


def dump_monitor(db_path: Path):
    """Dump all monitor table entries."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    print(f"\n=== Monitor Table: {db_path} ===\n")

    cursor.execute("SELECT id, timestamp, kind, substr(message, 1, 200) as msg FROM monitor ORDER BY id")
    rows = cursor.fetchall()

    if not rows:
        print("NO ENTRIES IN MONITOR TABLE!")
        return

    for row in rows:
        id_, ts, kind, msg = row
        # Truncate message for display
        msg_display = msg[:150] + "..." if len(msg) > 150 else msg
        print(f"[{id_:3d}] {ts:.3f} | {kind:12s} | {msg_display}")

    print(f"\nTotal: {len(rows)} entries")

    # Check for specific issues
    print("\n=== Analysis ===")

    cursor.execute("SELECT message FROM monitor WHERE kind = 'status' ORDER BY id DESC LIMIT 1")
    status_row = cursor.fetchone()
    print(f"Latest status: {status_row[0] if status_row else 'NONE'}")

    cursor.execute("SELECT COUNT(*) FROM monitor WHERE kind = 'lock'")
    lock_count = cursor.fetchone()[0]
    print(f"Lock events: {lock_count}")

    cursor.execute("SELECT COUNT(*) FROM monitor WHERE kind = 'lease'")
    lease_count = cursor.fetchone()[0]
    print(f"Lease events: {lease_count}")

    cursor.execute("SELECT COUNT(*) FROM monitor WHERE kind = 'error'")
    error_count = cursor.fetchone()[0]
    print(f"Error events: {error_count}")

    if error_count > 0:
        print("\n=== Errors ===")
        cursor.execute("SELECT message FROM monitor WHERE kind = 'error'")
        for row in cursor.fetchall():
            print(row[0][:500])

    # Check for locks that are pending
    cursor.execute("SELECT message FROM monitor WHERE kind = 'lock'")
    for row in cursor.fetchall():
        print(f"\n=== Lock Detail ===\n{row[0][:1000]}")

    conn.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnose_job.py <job_id> [exec_dir]")
        print("\nExample: python diagnose_job.py 69a5aacd1119f48b60fda161")
        sys.exit(1)

    job_id = sys.argv[1]

    global EXEC_DIR
    if len(sys.argv) > 2:
        EXEC_DIR = Path(sys.argv[2])

    print(f"Looking for job: {job_id}")
    print(f"Exec dir: {EXEC_DIR}")

    db_path = find_job(job_id)

    if not db_path:
        print(f"ERROR: Job '{job_id}' not found!")
        print("\nAvailable jobs:")
        for user_dir in EXEC_DIR.iterdir():
            if not user_dir.is_dir():
                continue
            for job_dir in user_dir.iterdir():
                if job_dir.is_dir() and (job_dir / DB_FILE).exists():
                    print(f"  {job_dir.name}")
        sys.exit(1)

    dump_monitor(db_path)


if __name__ == "__main__":
    main()
