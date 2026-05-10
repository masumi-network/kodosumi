import json
import os
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Dict, List, Optional
import ssl

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import dotenv

dotenv.load_dotenv()

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_MASUMI_RE = re.compile(r"^(?:KODO_)?MASUMI(\d*)$")


@dataclass
class MasumiConfig:
    """Per-network Masumi payment configuration."""
    network: str
    base_url: str
    token: str
    pay_by_time: float = 1200.0  # seconds - payment deadline (default 20 minutes)
    submit_result_by_time: float = 3600.0  # seconds - result submission deadline (default 60 minutes)
    poll_interval: float = 1.0  # seconds - interval between payment status polls

    @property
    def registry_network(self) -> str:
        """Map internal network name to Masumi API network (Preprod|Mainnet)."""
        if self.network.startswith("Mainnet"):
            return "Mainnet"
        return "Preprod"


def _parse_masumi_env(default_masumi: str = "") -> Dict[str, MasumiConfig]:
    """
    Scan os.environ for Masumi network configurations.

    Accepts: MASUMI, MASUMI0..MASUMI9, KODO_MASUMI, KODO_MASUMI0..KODO_MASUMI9
    Format: "Name URL key [pay_by_time] [submit_result_by_time] [poll_interval]"
    KODO_-prefixed vars take precedence over bare ones for the same suffix.

    Args:
        default_masumi: Fallback value for the unsuffixed MASUMI config
            (used when the Pydantic default isn't in os.environ).
    """
    # Collect by suffix: "" or "0".."9", preferring KODO_ prefixed
    by_suffix: Dict[str, str] = {}
    for key, val in os.environ.items():
        m = _MASUMI_RE.match(key)
        if not m:
            continue
        suffix = m.group(1)  # "" or "0".."9"
        is_prefixed = key.startswith("KODO_")
        if suffix in by_suffix and not is_prefixed:
            continue  # KODO_ version already stored, skip bare
        by_suffix[suffix] = val

    # Use Pydantic default for unsuffixed MASUMI when not in os.environ
    if "" not in by_suffix and default_masumi:
        by_suffix[""] = default_masumi

    networks: Dict[str, MasumiConfig] = {}
    for suffix, val in sorted(by_suffix.items()):
        parts = val.split()
        if len(parts) < 3 or len(parts) > 6:
            raise ValueError(
                f"MASUMI{suffix} requires 3-6 space-separated values: "
                f"Name URL key [pay_by_time] [submit_result_by_time] "
                f"[poll_interval] (got {len(parts)})"
            )
        extras = {}
        if len(parts) > 3:
            extras["pay_by_time"] = float(parts[3])
        if len(parts) > 4:
            extras["submit_result_by_time"] = float(parts[4])
        if len(parts) > 5:
            extras["poll_interval"] = float(parts[5])
        cfg = MasumiConfig(
            network=parts[0],
            base_url=parts[1],
            token=parts[2],
            **extras,
        )
        networks[cfg.network] = cfg
    return networks


class Settings(BaseSettings):

    EXEC_DIR: str = "./data/execution"

    SPOOLER_LOG_FILE: str = "./data/spooler.log"
    SPOOLER_LOG_FILE_LEVEL: str = "DEBUG"
    SPOOLER_STD_LEVEL: str = "INFO"
    SPOOLER_LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB per file
    SPOOLER_LOG_BACKUP_COUNT: int = 5  # Keep 5 backup files

    UPLOAD_DIR: str = "./data/uploads"    
    RAY_SERVER: str = "localhost:6379"
    RAY_DASHBOARD: str = "http://localhost:8265"
    RAY_SERVE_ADDRESS: str = "http://localhost:8005"  # Ray Serve HTTP endpoint
    SERVE_EXECUTABLE: str = "serve"
    
    APP_LOG_FILE: str = "./data/app.log"
    APP_LOG_FILE_LEVEL: str = "DEBUG"
    APP_STD_LEVEL: str = "INFO"
    APP_LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB per file
    APP_LOG_BACKUP_COUNT: int = 5  # Keep 5 backup files
    CORS_ORIGINS: List[str] = ["*"]

    # Audit log for boot/deployment events
    AUDIT_LOG_FILE: str = "./data/audit.log"
    AUDIT_LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB per file
    AUDIT_LOG_BACKUP_COUNT: int = 5  # Keep 5 backup files

    APP_SERVER: str = "http://localhost:3370"
    SUMI_ADDRESS: str = ""  # Public URL for Sumi API (fallback to APP_SERVER if empty)
    APP_RELOAD: bool = False

    UVICORN_LEVEL: str = "WARNING"
    SECRET_KEY: str = "top secret -- change this in production"

    WAIT_FOR_JOB: int = 600  # seconds - how long to wait for job database to appear
    SPOOLER_INTERVAL: float = 0.25  # seconds - interval between spooler main loop iterations
    SPOOLER_BATCH_SIZE: int = 10  # number of events to batch retrieve
    SPOOLER_BATCH_TIMEOUT: float = 0.1  # seconds - timeout for batch retrieval operations
    
    ADMIN_DATABASE: str = "sqlite+aiosqlite:///./data/admin.db"
    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD: str = "admin"

    REGISTER_FLOW: list[str] = []
    PROXY_TIMEOUT: int = 30  # seconds - HTTP proxy request timeout

    # Boot process timeout - how long to wait for Ray Serve deployments
    # Default 30 minutes (1800 seconds) because apps may install their own virtualenvs
    BOOT_HEALTH_TIMEOUT: int = 1800  # seconds

    YAML_BASE: str = "./data/config/config.yaml"

    SSL_KEYFILE: Optional[str] = None
    SSL_CERTFILE: Optional[str] = None
    SSL_KEYFILE_PASSWORD: Optional[str] = None
    SSL_VERSION: int = ssl.PROTOCOL_TLS_SERVER
    SSL_CERT_REQS: int = ssl.CERT_NONE
    SSL_CA_CERTS: Optional[str] = None
    SSL_CIPHERS: str = "TLSv1"

    APP_WORKERS: int = 1

    # fix this: remove the hex
    ANONYMOUS: str = "_ANONYMOUS_"

    LOCK_EXPIRES: float = 10800  # seconds (3 hours) - how long a lock remains valid
    CHUNK_SIZE: int = 5 * 1024 * 1024  # bytes - chunk size for file operations
    SAVE_CHUNK_SIZE: int = 1024 * 1024  # bytes - chunk size for saving files

    # Masumi payment integration
    # Format: "Name URL key [pay_by_time] [submit_result_by_time] [poll_interval]"
    # MASUMI0..MASUMI9 support multiple networks (e.g. Preprod + Mainnet)
    MASUMI: str = ""
    MASUMI0: str = ""
    MASUMI1: str = ""
    MASUMI2: str = ""
    MASUMI3: str = ""
    MASUMI4: str = ""
    MASUMI5: str = ""
    MASUMI6: str = ""
    MASUMI7: str = ""
    MASUMI8: str = ""
    MASUMI9: str = ""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="KODO_",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("EXEC_DIR", mode="before")
    def make_dir(cls, v):
        if v:
            Path(v).mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("SPOOLER_LOG_FILE", "YAML_BASE", "AUDIT_LOG_FILE", mode="before")
    def make_parent(cls, v):
        if v:
            Path(v).parent.mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("CORS_ORIGINS", "REGISTER_FLOW", mode="before")
    def string_to_list(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(',')]
        return v

    @cached_property
    def masumi_networks(self) -> Dict[str, MasumiConfig]:
        """Parse MASUMI env vars into per-network configs."""
        return _parse_masumi_env(default_masumi=self.MASUMI)

    @property
    def masumi_network_names(self) -> List[str]:
        """Available Masumi network names (for UI dropdowns)."""
        return list(self.masumi_networks.keys())

    @property
    def sumi_address(self) -> str:
        """Public URL for Sumi API (SUMI_ADDRESS or fallback to APP_SERVER)."""
        return self.SUMI_ADDRESS or self.APP_SERVER

    def get_masumi(self, network: str) -> MasumiConfig:
        """Get Masumi config for a specific network."""
        networks = self.masumi_networks
        if network not in networks:
            available = list(networks.keys())
            raise ValueError(
                f"No Masumi config for network '{network}'. "
                f"Available: {available}. "
                f"Set KODO_MASUMI0=\"{network} <url> <key> "
                f"[<pay_by_time> [<submit_result_by_time> [<poll_interval>]]]\""
            )
        return networks[network]


class InternalSettings(Settings):

    def __init__(self, **kwargs):
        for field in self.__class__.model_fields:
            env_var = f"iKODO_{field}"
            if env_var in os.environ and field not in kwargs:
                kwargs[field] = json.loads(os.environ[env_var])
        super().__init__(**kwargs)
        for field in self.__class__.model_fields:
            if _MASUMI_RE.match(field):
                val = getattr(self, field, "")
                if val:
                    os.environ.setdefault(f"KODO_{field}", val)
