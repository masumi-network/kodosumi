import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path  # !/usr/bin/env python3
from tests.test_exec import start_ray

from kodosumi.config import Settings


def test_spooler_blocking_mode(tmp_path: Path, start_ray):
    proc = subprocess.Popen(
        [sys.executable, "-m", "kodosumi.cli", "spool", 
            "--block", f"--exec-dir={tmp_path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(5)
    assert proc.poll() is None

    proc2 = subprocess.Popen(
        [sys.executable, "-m", "kodosumi.cli", "spool", 
            "--block", f"--exec-dir={tmp_path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, stderr = proc2.communicate()
    check = stderr.decode().lower()
    print(check)
    assert "connecting to existing ray" in check
    assert "spooler already running" in check
    assert "spooler shutdown, please wait" in check
    
    proc.send_signal(signal.SIGTERM)
    for _ in range(5):
        if proc.poll() is not None:
            break
        time.sleep(1)
    assert proc.poll() is not None
    _, stderr = proc.communicate()
    check = stderr.decode().lower()
    assert "connecting to existing ray" in check
    assert "spooler started" in check
    assert "spooler shutdown, please wait" in check


def test_spooler_daemon_mode(tmp_path: Path, start_ray):
    subprocess.run(
        [sys.executable, "-m", "kodosumi.cli", "spool", 
            f"--exec-dir={tmp_path}"],
        check=True
    )        
    time.sleep(5)
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "kodosumi.cli", "spool", 
            "--block", f"--exec-dir={tmp_path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    _, stderr = proc2.communicate()
    check = stderr.decode().lower()
    print(check)
    assert "connecting to existing ray" in check
    assert "spooler already running" in check
    assert "spooler shutdown, please wait" in check
   
    proc3 = subprocess.Popen(
        [sys.executable, "-m", "kodosumi.cli", "spool", 
            "--stop", f"--exec-dir={tmp_path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    _, stderr = proc3.communicate()
    check = stderr.decode().lower()
    print(check)
    time.sleep(2)
    assert "connecting to existing ray" in check
    assert "spooler already running" not in check
    assert "spooler shutdown, please wait" not in check
    assert "spooler stopped with pid" in check
    
        
