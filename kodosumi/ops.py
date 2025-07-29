import tempfile
from subprocess import Popen, PIPE, STDOUT
import shutil
from typing import Dict
import sys
import os
import yaml
import traceback
import httpx
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
import yaml
from kodosumi.config import InternalSettings
from kodosumi.const import DB_FILE, DB_ARCHIVE

def _find_serve():
    return str(Path(sys.executable).parent.joinpath("serve"))


def build_config(filename: str) -> dict:
    file = Path(filename)
    config = yaml.safe_load(file.open())
    config_path = file.parent
    assert config_path.exists()
    apps = []
    failed = {}
    print("prepare applications:")
    for app in config_path.iterdir():
        if app.is_file() and app.suffix == ".yaml" and app.name != file.name:
            print(f"- {app}")
            try:
                apps.append(yaml.safe_load(app.open()))
            except Exception as exc:
                failed[app] = traceback.format_exc()
    if failed:
        print("failed:")
        for app, exc in failed.items():
            print(f"- {app}:")
            print(f"{exc}")
    config["applications"] = apps
    return config

def deploy(filename: str):
    fd, tmp = tempfile.mkstemp(suffix=".yaml", text=True)
    config = build_config(filename)
    with Path(tmp).open("w") as f:
        f.write(yaml.dump(config))
    print("start deployment:")
    proc = Popen([_find_serve(), "deploy", str(tmp)], stdout=PIPE, 
                 stderr=STDOUT)
    (out, _) = proc.communicate()
    if proc.returncode != 0:
        print(out.decode("utf-8"))
        print(f"- deployment failed.")
    else:
        print(f"- deployment succeeded.")

def shutdown():
    print("stop serve:")
    proc = Popen([_find_serve(), "shutdown", "-y"], stdout=PIPE, stderr=STDOUT)
    (out, _) = proc.communicate()
    if proc.returncode != 0:
        print(out.decode("utf-8"))
        print(f"- shutdown failed.")
    else:
        print(f"- shutdown succeeded.")


def status() -> Dict:
    proc = Popen([_find_serve(), "status"], stdout=PIPE, stderr=STDOUT)
    (out, _) = proc.communicate()
    application = yaml.safe_load(out.decode("utf-8"))
    status = application.get("applications", {})
    return { k: v["status"] for k, v in status.items() }


def vacuum():
    settings = InternalSettings()
    root = Path(settings.EXEC_DIR)
    for uid in root.iterdir():
        if uid.is_dir():
            print(f"- vacuum {uid.name}")
            for fid in uid.iterdir():
                if fid.is_dir():
                    db_file = fid.joinpath(DB_FILE)
                    db_archive = db_file.with_suffix(db_file.suffix + DB_ARCHIVE)
                    if not db_file.exists():
                        if db_archive.exists():
                            print(f"  - remove archived {fid.name}")
                        else:
                            print(f"  - unknown {fid}")
                        shutil.rmtree(str(fid))


if __name__ == "__main__":
    vacuum()