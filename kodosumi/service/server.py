import json
import logging
import os
import subprocess
import sys
import urllib

import uvicorn

import kodosumi
from kodosumi.config import Settings
from kodosumi.log import LOG_FORMAT, logger, slog


def _git_sha(repo_path: str) -> str:
    """Resolve git short SHA for the repo at *repo_path*.

    Resolution order:
      1. ``KODO_GIT_SHA`` environment variable (set by infra at deploy time)
      2. ``git rev-parse --short HEAD`` subprocess (works in local dev)
      3. ``"unknown"`` on any failure — never blocks startup.
    """
    env_sha = os.environ.get("KODO_GIT_SHA", "").strip()
    if env_sha:
        return env_sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def _build_startup_banner(settings: Settings) -> dict:
    """Return a dict of startup banner fields (pure, side-effect-free)."""
    repo_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return {
        "version": kodosumi.__version__,
        "python": sys.version.split()[0],
        "git_sha": _git_sha(repo_path),
        "app_server": settings.APP_SERVER,
        "ray_server": settings.RAY_SERVER,
        "ray_dashboard": settings.RAY_DASHBOARD,
        "ray_serve": settings.RAY_SERVE_ADDRESS,
        "exec_dir": settings.EXEC_DIR,
    }


def run(settings: Settings):
    server = urllib.parse.urlparse(settings.APP_SERVER)
    if server.hostname is None:
        raise ValueError("Invalid app server URL, missing hostname")
    if server.port is None:
        raise ValueError("Invalid app server URL, missing port")
    for k, v in settings.model_dump().items():
        if v is not None:
            os.environ[f"iKODO_{k}"] = json.dumps(v)
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": LOG_FORMAT,
                "use_colors": None,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "": {"handlers": ["default"], "level": settings.UVICORN_LEVEL},
            "uvicorn.error": {"level": settings.UVICORN_LEVEL},
            "uvicorn.access": {"level": settings.UVICORN_LEVEL},
        },
    }

    # Emit startup banner before handing off to uvicorn.
    try:
        banner = _build_startup_banner(settings)
        slog(
            logger,
            logging.INFO,
            "kodosumi.startup",
            version=banner["version"],
            python=banner["python"],
            git_sha=banner["git_sha"],
            app_server=banner["app_server"],
            ray=banner["ray_server"],
            dashboard=banner["ray_dashboard"],
            ray_serve=banner["ray_serve"],
            exec_dir=banner["exec_dir"],
        )
    except Exception:
        pass  # never block startup

    uvicorn.run(
        "kodosumi.service.app:create_app",
        host=server.hostname,
        port=server.port,
        reload=settings.APP_RELOAD,
        factory=True,
        log_config=log_config,
        access_log=settings.UVICORN_ACCESS_LOG,
        headers=[("server", "kodosumi service")],
        ssl_keyfile=settings.SSL_KEYFILE,
        ssl_certfile=settings.SSL_CERTFILE,
        ssl_keyfile_password=settings.SSL_KEYFILE_PASSWORD,
        ssl_version=settings.SSL_VERSION,
        ssl_cert_reqs=settings.SSL_CERT_REQS,
        ssl_ca_certs=settings.SSL_CA_CERTS,
        ssl_ciphers=settings.SSL_CIPHERS,
        workers=settings.APP_WORKERS
    )
    

if __name__ == "__main__":
    run(Settings())
