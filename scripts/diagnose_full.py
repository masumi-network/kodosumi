#!/usr/bin/env python3
"""
Full diagnostic for job issues including Ray actor state.
Run this on the cluster: python scripts/diagnose_full.py <job_id>
"""

import json
import sqlite3
import sys
from pathlib import Path

# Initialize Ray connection
try:
    import ray
    ray.init(address="auto", ignore_reinit_error=True)
    RAY_AVAILABLE = True
except Exception as e:
    print(f"Warning: Could not connect to Ray: {e}")
    RAY_AVAILABLE = False

EXEC_DIR = Path("./data/execution")
DB_FILE = "sqlite3.db"
NAMESPACE = "kodosumi"


def find_job_db(job_id: str) -> Path | None:
    """Find job database across all user directories."""
    for user_dir in EXEC_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        db_path = user_dir / job_id / DB_FILE
        if db_path.exists():
            return db_path
    return None


def check_ray_actor(job_id: str):
    """Check if Ray actor exists and get its state."""
    if not RAY_AVAILABLE:
        print("\n=== Ray Actor ===")
        print("Ray not available - cannot check actor state")
        return None

    print(f"\n=== Ray Actor: {job_id} ===")
    try:
        actor = ray.get_actor(job_id, namespace=NAMESPACE)
        print(f"Actor found: {actor}")

        # Try to get locks
        try:
            locks = ray.get(actor.get_locks.remote(), timeout=5)
            print(f"Locks: {json.dumps(locks, indent=2, default=str)}")
        except Exception as e:
            print(f"get_locks failed: {e}")

        # Try to get status
        try:
            status = ray.get(actor.get_status.remote(), timeout=5)
            print(f"Status: {status}")
        except Exception as e:
            print(f"get_status failed: {e}")

        return actor
    except Exception as e:
        print(f"Actor NOT found: {e}")
        return None


def analyze_monitor(db_path: Path):
    """Analyze monitor table in detail."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    print(f"\n=== Monitor Table: {db_path} ===\n")

    # Get all entries
    cursor.execute("SELECT id, timestamp, kind, message FROM monitor ORDER BY id")
    rows = cursor.fetchall()

    if not rows:
        print("NO ENTRIES IN MONITOR TABLE!")
        return

    # Group by kind
    by_kind = {}
    for id_, ts, kind, msg in rows:
        if kind not in by_kind:
            by_kind[kind] = []
        by_kind[kind].append((id_, ts, msg))

    print(f"Total entries: {len(rows)}")
    print(f"Entry kinds: {list(by_kind.keys())}")
    print()

    # Show status entries
    if 'status' in by_kind:
        print("=== Status Events ===")
        for id_, ts, msg in by_kind['status']:
            print(f"  [{id_}] {ts:.3f} | {msg}")
        print()

    # Show lock/lease entries (CRITICAL for HITL)
    if 'lock' in by_kind or 'lease' in by_kind:
        print("=== Lock/Lease Events (HITL) ===")

        locks = set()
        for id_, ts, msg in by_kind.get('lock', []):
            try:
                d = json.loads(msg)
                lid = d.get("dict", {}).get("lid")
                locks.add(lid)
                print(f"  [LOCK  {id_}] {ts:.3f} | lid={lid}")
                print(f"           app_url={d.get('dict', {}).get('app_url')}")
            except:
                print(f"  [LOCK  {id_}] {ts:.3f} | {msg[:100]}")

        for id_, ts, msg in by_kind.get('lease', []):
            try:
                d = json.loads(msg)
                lid = d.get("dict", {}).get("lid")
                locks.discard(lid)
                print(f"  [LEASE {id_}] {ts:.3f} | lid={lid}")
            except:
                print(f"  [LEASE {id_}] {ts:.3f} | {msg[:100]}")

        print(f"\n  Pending locks: {locks if locks else 'NONE'}")
        print()

    # Show error entries
    if 'error' in by_kind:
        print("=== Error Events ===")
        for id_, ts, msg in by_kind['error']:
            print(f"  [{id_}] {ts:.3f}")
            print(f"         {msg[:500]}")
        print()

    # Show meta entry
    if 'meta' in by_kind:
        print("=== Meta ===")
        for id_, ts, msg in by_kind['meta']:
            try:
                d = json.loads(msg)
                extra = d.get("dict", {}).get("extra", {})
                print(f"  identifier_from_purchaser: {extra.get('identifier_from_purchaser')}")
                print(f"  agentIdentifier: {extra.get('agentIdentifier')}")
                print(f"  sumi_endpoint: {extra.get('sumi_endpoint')}")
                print(f"  network: {extra.get('network')}")
            except:
                print(f"  {msg[:200]}")
        print()

    # Show payment entries
    if 'payment' in by_kind:
        print("=== Payment Events ===")
        for id_, ts, msg in by_kind['payment']:
            try:
                d = json.loads(msg)
                step = d.get("dict", {}).get("step")
                print(f"  [{id_}] {ts:.3f} | step={step}")
            except:
                print(f"  [{id_}] {ts:.3f} | {msg[:100]}")
        print()

    # Compute expected status
    latest_status = by_kind.get('status', [])[-1][2] if 'status' in by_kind else None
    locks = set()
    for _, _, msg in by_kind.get('lock', []):
        try:
            d = json.loads(msg)
            locks.add(d.get("dict", {}).get("lid"))
        except:
            pass
    for _, _, msg in by_kind.get('lease', []):
        try:
            d = json.loads(msg)
            locks.discard(d.get("dict", {}).get("lid"))
        except:
            pass

    print("=== Diagnosis ===")
    print(f"Latest Kodosumi status: {latest_status}")
    print(f"Pending locks: {locks}")

    # Determine MIP-003 status
    if latest_status == "finished":
        mip_status = "completed"
    elif latest_status == "error":
        mip_status = "failed"
    elif latest_status == "payment":
        mip_status = "awaiting_payment"
    elif locks:
        mip_status = "awaiting_input"
    else:
        mip_status = "running"

    print(f"Expected MIP-003 status: {mip_status}")

    if mip_status == "awaiting_input" and locks:
        print(f"\nWARNING: Job is awaiting input with locks: {locks}")
        print("If input_schema is null in API response, the Ray actor may be dead!")

    conn.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnose_full.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    print(f"=== Full Diagnosis for Job: {job_id} ===")

    # Find and analyze job database
    db_path = find_job_db(job_id)
    if db_path:
        analyze_monitor(db_path)
    else:
        print(f"\nERROR: Job database not found for {job_id}")

    # Check Ray actor state
    check_ray_actor(job_id)


if __name__ == "__main__":
    main()
