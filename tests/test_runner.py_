import asyncio
import sqlite3
from pathlib import Path

import pytest
import ray

from kodosumi.helper import now
from kodosumi.runner.main import create_runner
from kodosumi.spooler import Spooler, DB_FILE


# For testing, we need a dummy entry point function.
def dummy_func(inputs):
    # A simple synchronous function that returns a constant result.
    return "dummy_result"

@pytest.fixture(scope="session", autouse=True)
def init_ray():
    # Initialize Ray in local mode for testing.
    ray.init(ignore_reinit_error=True)
    yield
    ray.shutdown()

@pytest.mark.asyncio
async def test_runner_enqueue_and_get_batch():
    fid, runner = create_runner(
        username="test_user",
        base_url="http://dummy:8000",
        entry_point=dummy_func,
        inputs={"test": "value"})
    await runner.put.remote("test", "message1")
    await runner.put.remote("test", "message2")
    batch = ray.get(runner.get_batch.remote(10, 0.1))
    assert len(batch) >= 2, f"Expected at least 2 messages, got {len(batch)}"
    await runner.shutdown.remote()


@pytest.mark.asyncio
async def test_runner_run_and_shutdown():
    fid, runner = create_runner(
        username="test_user",
        base_url="http://dummy:8000",
        entry_point=dummy_func,
        inputs={"x": 42}
    )
    runner.run.remote()
    active = ray.get(runner.is_active.remote())
    assert active is True
    output = []
    while True:
        msg = ray.get(runner.get_batch.remote())
        output += msg
        active = ray.get(runner.is_active.remote())
        if not active:
            break
        await asyncio.sleep(0.1)
    result = ray.get(runner.shutdown.remote())
    assert result == "Runner shutdown complete."
    status = [o["payload"] for o in output if o["kind"] == "status"]
    assert status == ['starting', 'running', 'finished']


@pytest.mark.asyncio
async def test_spooler_retrieve(tmp_path: Path):
    # Create a temporary directory for spooler output.
    exec_dir = tmp_path / "data"
    exec_dir.mkdir()
    # Create a runner actor.
    fid, runner = create_runner(
        username="test_user",
        base_url="http://dummy:8000",
        entry_point=dummy_func,
        inputs={"x": 42}
    )
    # Enqueue some messages in the runner.
    for i in range(5):
        runner.put.remote(  # type: ignore
            "test", f"msg {i}"
        )
    # Create a Spooler instance with short intervals for testing.
    spooler = Spooler(
        exec_dir=str(exec_dir),
        interval=1,          # Poll every 1 second.
        batch_size=2,
        batch_timeout=0.1,
        force=True
    )
    # Run the spooler in background.
    spooler_task = asyncio.create_task(spooler.start())
    # Allow the spooler to run for a few seconds.
    await asyncio.sleep(3)
    # Signal the spooler to shutdown.
    await spooler.shutdown()
    # Cancel the background task.
    spooler_task.cancel()
    try:
        await spooler_task
    except asyncio.CancelledError:
        pass
    # Verify that a database file was created for the runner.
    db_path = exec_dir.joinpath("test_user", fid, DB_FILE)
    assert db_path.exists(), f"Database file {db_path} should exist."
    # Open the database and count the persisted records.
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM monitor")
    count = cursor.fetchone()[0]
    conn.close()
    assert count >= 5, f"Expected at least 5 records, got {count}"
