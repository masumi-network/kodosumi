#!/usr/bin/env python3
"""
Diagnose why Ray actors are PENDING.

Run on Ray head:
    python scripts/diagnose_ray_pending.py
    python scripts/diagnose_ray_pending.py 69a5f4f71119f49ba5de5891  # specific job
"""

import sys
import ray
from datetime import datetime

NAMESPACE = "kodosumi"
JOB_ID = sys.argv[1] if len(sys.argv) > 1 else None

def main():
    ray.init(address="auto", ignore_reinit_error=True)

    print("=" * 60)
    print(f"RAY CLUSTER DIAGNOSTIC - {datetime.now()}")
    print("=" * 60)

    # 1. Cluster resources
    print("\n=== CLUSTER RESOURCES ===")
    resources = ray.cluster_resources()
    available = ray.available_resources()
    print(f"Total CPU: {resources.get('CPU', 0)}")
    print(f"Available CPU: {available.get('CPU', 0)}")
    print(f"Total Memory: {resources.get('memory', 0) / 1e9:.1f} GB")
    print(f"Available Memory: {available.get('memory', 0) / 1e9:.1f} GB")
    print(f"Total Object Store: {resources.get('object_store_memory', 0) / 1e9:.1f} GB")
    print(f"Available Object Store: {available.get('object_store_memory', 0) / 1e9:.1f} GB")

    # 2. All actors - use state API
    print("\n=== ALL ACTORS (state API) ===")
    try:
        from ray.util.state import list_actors
        actors = list_actors(filters=[("state", "!=", "DEAD")], limit=100)

        pending = []
        alive = []

        for actor in actors:
            state = actor.get("state", "UNKNOWN")
            name = actor.get("name", "unnamed")
            class_name = actor.get("class_name", "Unknown")
            actor_id = actor.get("actor_id", "?")

            info = {
                "name": name,
                "class": class_name,
                "state": state,
                "id": actor_id[:12] if actor_id else "?",
            }

            if "PENDING" in state:
                pending.append(info)
            else:
                alive.append(info)

        print(f"\nPENDING actors ({len(pending)}):")
        for a in pending:
            print(f"  {a['state']:25s} | {a['class']:20s} | {a['name'][:30]:30s} | {a['id']}")

        print(f"\nALIVE actors ({len(alive)}):")
        for a in alive[:20]:  # Limit output
            print(f"  {a['state']:25s} | {a['class']:20s} | {a['name'][:30]:30s} | {a['id']}")
        if len(alive) > 20:
            print(f"  ... and {len(alive) - 20} more")

    except ImportError:
        print("ray.util.state not available - using legacy API")
        # Fallback: try to get actors from namespace
        try:
            actors = ray.util.list_named_actors(all_namespaces=True)
            print(f"Named actors: {len(actors)}")
            for name in actors[:20]:
                print(f"  {name}")
        except:
            print("Could not list actors")

    # 3. Check specific job actors
    print("\n=== KODOSUMI NAMESPACE ACTORS ===")
    try:
        from ray.util.state import list_actors
        kodo_actors = list_actors(
            filters=[("ray_namespace", "=", NAMESPACE)],
            limit=50
        )

        runners = [a for a in kodo_actors if a.get("class_name") == "Runner"]
        queues = [a for a in kodo_actors if "_Queue" in a.get("class_name", "")]

        print(f"\nRunners ({len(runners)}):")
        for r in runners:
            print(f"  {r.get('state'):20s} | {r.get('name', '?')}")

        print(f"\nQueue Actors ({len(queues)}):")
        for q in queues:
            print(f"  {q.get('state'):20s} | {q.get('class_name', '?')}")

    except Exception as e:
        print(f"Error: {e}")

    # 4. Check pending tasks
    print("\n=== PENDING PLACEMENT GROUPS ===")
    try:
        from ray.util.state import list_placement_groups
        pgs = list_placement_groups(filters=[("state", "=", "PENDING")])
        print(f"Pending placement groups: {len(pgs)}")
        for pg in pgs[:5]:
            print(f"  {pg}")
    except Exception as e:
        print(f"Could not check: {e}")

    # 5. Node status
    print("\n=== NODES ===")
    try:
        nodes = ray.nodes()
        for node in nodes:
            state = "ALIVE" if node.get("Alive") else "DEAD"
            resources = node.get("Resources", {})
            print(f"  {state:6s} | CPU: {resources.get('CPU', 0)} | Mem: {resources.get('memory', 0)/1e9:.1f}GB | {node.get('NodeManagerAddress', '?')}")
    except Exception as e:
        print(f"Error: {e}")

    # 6. Suggest fixes
    print("\n=== DIAGNOSIS ===")
    if available.get('CPU', 0) < 1:
        print("WARNING: No CPU available - actors will queue!")
    if available.get('memory', 0) < 1e9:
        print("WARNING: Low memory (<1GB) - actors may fail to start!")
    if pending:
        print(f"ISSUE: {len(pending)} actors in PENDING state")
        queue_pending = [p for p in pending if "_Queue" in p.get("class", "")]
        if queue_pending:
            print(f"  - {len(queue_pending)} Queue actors pending (blocks Runner init)")
        runner_pending = [p for p in pending if p.get("class") == "Runner"]
        if runner_pending:
            print(f"  - {len(runner_pending)} Runner actors pending")
    else:
        print("OK: No pending actors")

    print("\n" + "=" * 60)


def check_ray_logs():
    """Check Ray logs for errors."""
    import glob
    import os
    from pathlib import Path

    print("\n" + "=" * 60)
    print("RAY LOGS ANALYSIS")
    print("=" * 60)

    # Find Ray session directory
    ray_session = Path("/tmp/ray/session_latest")
    if not ray_session.exists():
        print(f"Ray session not found at {ray_session}")
        return

    logs_dir = ray_session / "logs"
    if not logs_dir.exists():
        print(f"Logs directory not found at {logs_dir}")
        return

    print(f"\nLogs directory: {logs_dir}")

    # Check for worker errors
    print("\n=== RECENT WORKER ERRORS ===")
    worker_logs = sorted(logs_dir.glob("worker-*.err"), key=os.path.getmtime, reverse=True)
    for log_file in worker_logs[:5]:
        size = log_file.stat().st_size
        if size > 0:
            print(f"\n--- {log_file.name} ({size} bytes) ---")
            with open(log_file) as f:
                content = f.read()
                # Show last 2000 chars or all if smaller
                if len(content) > 2000:
                    print(f"... (truncated, showing last 2000 chars)")
                    print(content[-2000:])
                else:
                    print(content)

    # Check raylet logs for scheduling issues
    print("\n=== RAYLET ERRORS (scheduling) ===")
    raylet_logs = list(logs_dir.glob("raylet.err"))
    for log_file in raylet_logs:
        size = log_file.stat().st_size
        if size > 0:
            print(f"\n--- {log_file.name} ({size} bytes) ---")
            with open(log_file) as f:
                lines = f.readlines()
                # Show last 50 lines
                for line in lines[-50:]:
                    if any(kw in line.lower() for kw in ["error", "pending", "fail", "resource", "schedul"]):
                        print(line.rstrip())

    # Check gcs_server for actor issues
    print("\n=== GCS SERVER ERRORS ===")
    gcs_logs = list(logs_dir.glob("gcs_server.err"))
    for log_file in gcs_logs:
        size = log_file.stat().st_size
        if size > 0:
            print(f"\n--- {log_file.name} ({size} bytes) ---")
            with open(log_file) as f:
                lines = f.readlines()
                for line in lines[-30:]:
                    if any(kw in line.lower() for kw in ["error", "fail", "actor", "pending"]):
                        print(line.rstrip())

    # Check dashboard logs
    print("\n=== DASHBOARD ERRORS ===")
    dash_logs = list(logs_dir.glob("dashboard*.err"))
    for log_file in dash_logs:
        size = log_file.stat().st_size
        if size > 100:  # Only if meaningful content
            print(f"\n--- {log_file.name} ({size} bytes) ---")
            with open(log_file) as f:
                content = f.read()[-1000:]
                print(content)

    # Find actor-specific logs
    print("\n=== ACTOR DEATH/RESTART LOGS ===")
    for pattern in ["*Runner*", "*actor*", "*kodosumi*"]:
        actor_logs = list(logs_dir.glob(pattern))
        for log_file in actor_logs[:3]:
            if log_file.suffix == ".err" and log_file.stat().st_size > 0:
                print(f"\n--- {log_file.name} ---")
                with open(log_file) as f:
                    print(f.read()[-1500:])


def search_job_in_logs(job_id: str):
    """Search for a specific job ID in all Ray logs."""
    import os
    from pathlib import Path

    print("\n" + "=" * 60)
    print(f"SEARCHING FOR JOB: {job_id}")
    print("=" * 60)

    ray_session = Path("/tmp/ray/session_latest")
    if not ray_session.exists():
        print(f"Ray session not found at {ray_session}")
        return

    logs_dir = ray_session / "logs"
    found_in = []

    # Search all log files
    for log_file in logs_dir.glob("*"):
        if log_file.is_file():
            try:
                with open(log_file, errors="ignore") as f:
                    content = f.read()
                    if job_id in content:
                        found_in.append(log_file)
                        print(f"\n--- FOUND IN: {log_file.name} ---")
                        # Find and print lines containing the job_id
                        for i, line in enumerate(content.split("\n")):
                            if job_id in line:
                                print(f"  L{i}: {line[:200]}")
            except Exception as e:
                pass

    if not found_in:
        print(f"Job ID '{job_id}' NOT FOUND in any Ray logs!")
        print("This suggests:")
        print("  1. Actor was never created (request didn't reach Ray)")
        print("  2. Logs were rotated/deleted")
        print("  3. Job ran on a different Ray cluster")
    else:
        print(f"\nFound in {len(found_in)} log files")

    # Also check if actor exists
    print("\n=== ACTOR LOOKUP ===")
    try:
        actor = ray.get_actor(job_id, namespace=NAMESPACE)
        print(f"Actor FOUND: {actor}")
    except ValueError as e:
        print(f"Actor NOT FOUND in Ray: {e}")


if __name__ == "__main__":
    main()
    check_ray_logs()
    if JOB_ID:
        search_job_in_logs(JOB_ID)
