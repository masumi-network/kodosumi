"""
Boot process implementation for Kodosumi.

This module handles the step-by-step boot process:
- (A) Start Ray Deployment
- (B) Ray Serve Health-Check
- (C) Register Flows
- (D) Retrieve Flow Details
- (E) Iterate Expose Items and update meta
"""

import asyncio
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import yaml

from kodosumi.helper import HTTPXClient
from kodosumi.log import get_audit_logger
from kodosumi.service.expose import db
from kodosumi.const import KODOSUMI_API, KODOSUMI_AUTHOR, KODOSUMI_ORGANIZATION
from kodosumi.config import InternalSettings

# Constants (also defined in control.py - kept in sync)
RAY_SERVE_CONFIG = "./data/serve_config.yaml"

def create_subprocess_exec(*args: str):
    """Create an asyncio subprocess for the given command."""
    settings = InternalSettings()
    return asyncio.create_subprocess_exec(
        settings.SERVE_EXECUTABLE, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )


def ensure_serve_config():
    """Ensure serve_config.yaml exists with defaults."""
    config_path = Path(RAY_SERVE_CONFIG)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        default_config = """# Kodosumi Ray Serve Configuration
proxy_location: EveryNode

http_options:
  host: 0.0.0.0
  port: 8005

grpc_options:
  port: 9000
  grpc_servicer_functions: []

logging_config:
  encoding: TEXT
  log_level: WARNING
  logs_dir: null
  enable_access_log: true
"""
        config_path.write_text(default_config)

# Configuration constants (defaults - prefer settings from config.py)
BOOT_HEALTH_TIMEOUT_DEFAULT = 1800  # seconds (30 minutes) - fallback if not configured
BOOT_HEALTH_TIMEOUT = BOOT_HEALTH_TIMEOUT_DEFAULT  # Alias for tests
BOOT_POLL_INTERVAL = 2  # seconds - interval between status polls during boot
BOOT_BATCH_SIZE = 5  # Max concurrent deployments in sliding window
BOOT_DEPLOY_POLL_INTERVAL = 5  # Seconds between status polls during sliding-window deploy

# Total number of main steps for progress tracking
BOOT_TOTAL_STEPS = 5  # A, B, C, D, E

# Ray Serve Application Status (from ray.serve.schema.ApplicationStatus)
FINAL_STATES = {"NOT_STARTED", "RUNNING", "UNHEALTHY", "DEPLOY_FAILED"}
TRANSITIONAL_STATES = {"DEPLOYING", "DELETING"}

# Default serve config template
DEFAULT_SERVE_CONFIG = {
    "proxy_location": "EveryNode",
    "http_options": {
        "host": "0.0.0.0",
        "port": 8005
    },
    "grpc_options": {
        "port": 9000,
        "grpc_servicer_functions": []
    },
    "logging_config": {
        "encoding": "TEXT",
        "log_level": "WARNING",
        "logs_dir": None,
        "enable_access_log": True
    }
}


def get_ray_serve_address_from_config(config_path: str = RAY_SERVE_CONFIG, fallback: str = "http://localhost:8005") -> str:
    """
    Extract Ray Serve HTTP address from serve_config.yaml.

    Reads http_options.host and http_options.port from the config.
    Falls back to the provided fallback address if config is missing or invalid.

    Args:
        config_path: Path to serve_config.yaml
        fallback: Default address if config is unavailable

    Returns:
        Ray Serve HTTP address (e.g., "http://localhost:8005")
    """
    path = Path(config_path)
    if not path.exists():
        return fallback

    try:
        with open(path, "r") as f:
            config = yaml.safe_load(f) or {}

        http_options = config.get("http_options", {})
        host = http_options.get("host", "localhost")
        port = http_options.get("port", 8005)

        # Convert 0.0.0.0 to localhost for client connections
        if host == "0.0.0.0":
            host = "localhost"

        return f"http://{host}:{port}"
    except Exception:
        return fallback


class BootStep(str, Enum):
    """Boot process main steps (phases)."""
    DEPLOY = "deploy"           # (A) Start Ray Deployment
    HEALTH = "health"           # (B) Ray Serve Health-Check
    REGISTER = "register"       # (C) Register Flows
    RETRIEVE = "retrieve"       # (D) Retrieve Flow Details
    UPDATE = "update"           # (E) Update Meta
    COMPLETE = "complete"
    ERROR = "error"


class MessageType(str, Enum):
    """Type of boot message."""
    STEP_START = "step_start"     # Starting a main step
    STEP_END = "step_end"         # Completed a main step
    ACTIVITY = "activity"         # Activity within a step
    RESULT = "result"             # Result/response from an activity
    INFO = "info"                 # General info
    WARNING = "warning"           # Warning message
    ERROR = "error"               # Error message
    PROGRESS = "progress"         # Progress update


@dataclass
class BootProgress:
    """Tracks overall boot progress."""
    current_step: int = 0
    total_steps: int = BOOT_TOTAL_STEPS
    step_name: str = ""
    activities_done: int = 0
    activities_total: int = 0

    @property
    def percent(self) -> int:
        """Calculate percentage complete."""
        if self.total_steps == 0:
            return 0
        base = (self.current_step / self.total_steps) * 100
        if self.activities_total > 0:
            step_progress = (self.activities_done / self.activities_total) * (100 / self.total_steps)
            return min(100, int(base + step_progress))
        return min(100, int(base))

    def to_dict(self) -> dict:
        return {
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "step_name": self.step_name,
            "activities_done": self.activities_done,
            "activities_total": self.activities_total,
            "percent": self.percent
        }


# Symbols for different message types
SYMBOLS = {
    "step_start": "▶",
    "step_end": "✓",
    "activity": "•",
    "result": "→",
    "info": "ℹ",
    "warning": "⚠",
    "error": "✗",
    "complete": "✔",
}


@dataclass
class BootMessage:
    """A message yielded during boot process."""
    step: BootStep
    msg_type: MessageType
    message: str
    timestamp: float = field(default_factory=time.time)
    target: Optional[str] = None      # e.g., expose name, URL
    result: Optional[str] = None      # e.g., status code, state
    progress: Optional[BootProgress] = None
    data: Optional[Dict[str, Any]] = None

    def __str__(self) -> str:
        """Format message for plain text console output with symbols."""
        if self.msg_type == MessageType.STEP_START:
            step_name = self.step.value.upper()
            return f"{SYMBOLS['step_start']} [{step_name}] {self.message}"

        elif self.msg_type == MessageType.STEP_END:
            if self.step == BootStep.COMPLETE:
                return f"{SYMBOLS['complete']} {self.message}"
            elif self.step == BootStep.ERROR:
                return f"{SYMBOLS['error']} {self.message}"
            else:
                return f"{SYMBOLS['step_end']} {self.message}"

        elif self.msg_type == MessageType.ERROR:
            return f"{SYMBOLS['error']} {self.message}"

        elif self.msg_type == MessageType.WARNING:
            if self.target:
                return f"{SYMBOLS['warning']} [{self.target}] {self.message}"
            return f"{SYMBOLS['warning']} {self.message}"

        elif self.msg_type == MessageType.INFO:
            if self.target:
                return f"{SYMBOLS['info']} [{self.target}] {self.message}"
            return f"{SYMBOLS['info']} {self.message}"

        elif self.msg_type == MessageType.RESULT:
            if self.target:
                return f"  [{self.target}] {self.message} {SYMBOLS['result']} {self.result}"
            return f"  {self.message} {SYMBOLS['result']} {self.result}"

        elif self.msg_type == MessageType.ACTIVITY:
            if self.target and self.result:
                return f"  {SYMBOLS['activity']} [{self.target}] {self.message} → {self.result}"
            elif self.target:
                return f"  {SYMBOLS['activity']} [{self.target}] {self.message}"
            elif self.result:
                return f"  {SYMBOLS['activity']} {self.message} → {self.result}"
            return f"  {SYMBOLS['activity']} {self.message}"

        else:
            if self.target:
                return f"  [{self.target}] {self.message}"
            return f"  {self.message}"

    def _is_success_result(self) -> bool:
        """Check if result indicates success."""
        if not self.result:
            return False
        result_lower = self.result.lower()
        return any(s in result_lower for s in ["ok", "success", "running", "alive", "200"])

    def _css_class(self) -> str:
        """Get CSS class for HTML formatting."""
        if self.msg_type == MessageType.STEP_START:
            return "step-header"
        elif self.msg_type == MessageType.STEP_END:
            if self.step in (BootStep.COMPLETE, BootStep.ERROR):
                return "step-complete" if self.step == BootStep.COMPLETE else "step-error"
            return "step-done"
        elif self.msg_type == MessageType.ERROR:
            return "error"
        elif self.msg_type == MessageType.WARNING:
            return "warning"
        elif self.msg_type == MessageType.INFO:
            return "info"
        elif self.msg_type == MessageType.RESULT:
            return "result success" if self._is_success_result() else "result"
        elif self.msg_type == MessageType.ACTIVITY:
            return "activity"
        return ""

    def to_html(self) -> str:
        """Format message as HTML with CSS classes for styling."""
        css_class = self._css_class()
        text = str(self)
        # Escape HTML special chars
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<span class="{css_class}">{text}</span>'

    def to_json(self) -> dict:
        """Convert to JSON-serializable dict for streaming."""
        return {
            "step": self.step.value,
            "type": self.msg_type.value,
            "message": self.message,
            "timestamp": self.timestamp,
            "target": self.target,
            "result": self.result,
            "progress": self.progress.to_dict() if self.progress else None,
            "text": str(self),
            "html": self.to_html(),
            "css_class": self._css_class()
        }


class BootLock:
    """
    Manages boot process lock to prevent concurrent boots.

    Uses a simple in-memory singleton pattern with optional
    file-based persistence for crash recovery.
    """

    _instance: Optional["BootLock"] = None
    _lock_file = Path("./data/boot.lock")

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._locked = False
            cls._instance._lock_time: Optional[float] = None
            cls._instance._owner: Optional[str] = None
            cls._instance._messages: List[BootMessage] = []
            cls._instance._subscribers: List[asyncio.Queue] = []
        return cls._instance

    @property
    def is_locked(self) -> bool:
        return self._locked

    @property
    def lock_time(self) -> Optional[float]:
        return self._lock_time

    @property
    def messages(self) -> List[BootMessage]:
        return self._messages.copy()

    def acquire(self, owner: str = "system", force: bool = False) -> bool:
        """
        Acquire the boot lock.

        Args:
            owner: Identifier for lock owner
            force: If True, forcibly acquire even if locked

        Returns:
            True if lock acquired, False if already locked
        """
        if self._locked and not force:
            return False

        self._locked = True
        self._lock_time = time.time()
        self._owner = owner
        self._messages = []
        self._subscribers = []

        # Create lock file
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file.write_text(f"{owner}:{self._lock_time}")

        return True

    def release(self) -> None:
        """Release the boot lock."""
        self._locked = False
        self._lock_time = None
        self._owner = None

        # Remove lock file
        if self._lock_file.exists():
            self._lock_file.unlink()

    async def add_message(self, msg: BootMessage) -> None:
        """Add a message and notify all subscribers."""
        self._messages.append(msg)
        # Notify all subscribers
        for queue in self._subscribers:
            try:
                await queue.put(msg)
            except:
                pass

    def subscribe(self) -> asyncio.Queue:
        """Subscribe to boot messages. Returns a queue that receives messages."""
        queue: asyncio.Queue = asyncio.Queue()
        # Send existing messages
        for msg in self._messages:
            queue.put_nowait(msg)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unsubscribe from boot messages."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)


# Global lock instance
boot_lock = BootLock()

# Background task reference (to prevent garbage collection)
_boot_task: Optional[asyncio.Task] = None


# =============================================================================
# Validation Functions - Check meta against reality
# =============================================================================

@dataclass
class ValidationResult:
    """Result of a validation check."""
    valid: bool
    message: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class MetaValidation:
    """Full validation result for an expose meta entry."""
    url: str
    app_running: Optional[ValidationResult] = None
    endpoint_alive: Optional[ValidationResult] = None
    fields_match: Optional[ValidationResult] = None

    @property
    def is_valid(self) -> bool:
        """True if all checks passed."""
        checks = [self.app_running, self.endpoint_alive, self.fields_match]
        return all(c is None or c.valid for c in checks)

    @property
    def issues(self) -> List[str]:
        """List of validation issues."""
        issues = []
        if self.app_running and not self.app_running.valid:
            issues.append(f"App: {self.app_running.message}")
        if self.endpoint_alive and not self.endpoint_alive.valid:
            issues.append(f"Endpoint: {self.endpoint_alive.message}")
        if self.fields_match and not self.fields_match.valid:
            issues.append(f"Fields: {self.fields_match.message}")
        return issues


async def check_app_running(
    ray_dashboard: str,
    app_name: str
) -> ValidationResult:
    """
    Check if application is RUNNING via Ray dashboard API.

    Fast check - just queries the dashboard status endpoint.

    Args:
        ray_dashboard: Ray dashboard URL (e.g., http://localhost:8265)
        app_name: Name of the application to check

    Returns:
        ValidationResult with status
    """
    import httpx

    url = f"{ray_dashboard.rstrip('/')}/api/serve/applications/"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return ValidationResult(
                    valid=False,
                    message=f"Dashboard returned {response.status_code}",
                    details={"status_code": response.status_code}
                )

            data = response.json()
            applications = data.get("applications", {})

            if app_name not in applications:
                return ValidationResult(
                    valid=False,
                    message=f"Application '{app_name}' not found",
                    details={"available_apps": list(applications.keys())}
                )

            app_info = applications[app_name]
            status = app_info.get("status", "UNKNOWN")

            if status == "RUNNING":
                return ValidationResult(
                    valid=True,
                    message="RUNNING",
                    details={"status": status}
                )
            else:
                return ValidationResult(
                    valid=False,
                    message=f"Status is {status}",
                    details={"status": status, "message": app_info.get("message", "")}
                )

    except httpx.TimeoutException:
        return ValidationResult(
            valid=False,
            message="Dashboard timeout",
            details={"error": "timeout"}
        )
    except Exception as e:
        return ValidationResult(
            valid=False,
            message=f"Dashboard error: {str(e)}",
            details={"error": str(e)}
        )


async def check_endpoint_alive(
    base_url: str,
    endpoint_path: str,
    timeout: float = 5.0
) -> ValidationResult:
    """
    Check if endpoint is alive via HEAD request.

    Args:
        base_url: Ray Serve base URL (e.g., http://localhost:8005)
        endpoint_path: Full path to endpoint (e.g., /my-app/run)
        timeout: Request timeout in seconds

    Returns:
        ValidationResult with status
    """
    import httpx

    url = f"{base_url.rstrip('/')}{endpoint_path}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.head(url)
            code = response.status_code

            if code in (200, 204, 405):
                # 405 = Method Not Allowed, but endpoint exists
                return ValidationResult(
                    valid=True,
                    message=f"Alive ({code})",
                    details={"status_code": code, "url": url}
                )
            elif code == 404:
                return ValidationResult(
                    valid=False,
                    message="Not found (404)",
                    details={"status_code": code, "url": url}
                )
            elif 500 <= code < 600:
                return ValidationResult(
                    valid=False,
                    message=f"Server error ({code})",
                    details={"status_code": code, "url": url}
                )
            else:
                # Other codes (3xx, 4xx) - consider alive but note the code
                return ValidationResult(
                    valid=True,
                    message=f"Responding ({code})",
                    details={"status_code": code, "url": url}
                )

    except httpx.TimeoutException:
        return ValidationResult(
            valid=False,
            message="Timeout",
            details={"error": "timeout", "url": url}
        )
    except Exception as e:
        return ValidationResult(
            valid=False,
            message=f"Error: {str(e)}",
            details={"error": str(e), "url": url}
        )


def parse_meta_data_yaml(data_yaml: Optional[str]) -> Dict[str, Any]:
    """
    Parse meta.data YAML string into dict.

    Extracts user-editable fields: name, description, tags, author.

    Args:
        data_yaml: YAML string from meta.data field

    Returns:
        Dict with parsed fields (empty dict if parsing fails)
    """
    if not data_yaml:
        return {}

    try:
        parsed = yaml.safe_load(data_yaml)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except yaml.YAMLError:
        return {}


def check_fields_match(
    meta_data: Dict[str, Any],
    flow_data: Dict[str, Any]
) -> ValidationResult:
    """
    Check if meta.data fields match flow data from GET /flow.

    Compares: name (display), description, tags, author (name),
    organization.

    Note: This checks if the user has customized the fields. If meta has
    values and they differ from flow defaults, that's intentional (user edited).
    This function flags when meta is EMPTY but flow has values (needs initial setup).

    Args:
        meta_data: Parsed meta.data dict
        flow_data: EndpointResponse dict from GET /flow

    Returns:
        ValidationResult indicating if fields need attention
    """
    missing_fields = []
    details = {}

    # Check name/display - meta uses 'display', flow uses 'summary'
    meta_name = meta_data.get("display") or meta_data.get("name", "")
    flow_summary = flow_data.get("summary", "")
    if not meta_name and flow_summary:
        missing_fields.append("name")
        details["name"] = {"meta": meta_name, "flow": flow_summary}

    # Check description
    meta_desc = meta_data.get("description", "")
    flow_desc = flow_data.get("description", "")
    if not meta_desc and flow_desc:
        missing_fields.append("description")
        details["description"] = {"meta": meta_desc, "flow": flow_desc}

    # Check tags
    meta_tags = meta_data.get("tags", [])
    flow_tags = flow_data.get("tags", [])
    if not meta_tags and flow_tags:
        missing_fields.append("tags")
        details["tags"] = {"meta": meta_tags, "flow": flow_tags}

    # Check author - meta has nested author.name, flow has author
    meta_author = meta_data.get("author", {})
    if isinstance(meta_author, dict):
        meta_name = meta_author.get("name", "")
        meta_org = meta_author.get("organization", "")
    else:
        meta_name = ""
        meta_org = ""

    flow_author = flow_data.get("author", "")
    flow_org = flow_data.get("organization", "")

    if not meta_name and flow_author:
        missing_fields.append("author.name")
        details["author"] = {"meta": meta_name, "flow": flow_author}

    if not meta_org and flow_org:
        missing_fields.append("author.organization")
        details["organization"] = {"meta": meta_org, "flow": flow_org}

    if missing_fields:
        return ValidationResult(
            valid=False,
            message=f"Missing: {', '.join(missing_fields)}",
            details=details
        )

    return ValidationResult(
        valid=True,
        message="All fields populated",
        details=details
    )


async def validate_meta_entry(
    meta_url: str,
    meta_data_yaml: Optional[str],
    ray_dashboard: str,
    ray_serve_address: str,
    flow_data: Optional[Dict[str, Any]] = None,
    check_app: bool = True,
    check_endpoint: bool = True,
    check_fields: bool = True
) -> MetaValidation:
    """
    Validate a single meta entry against reality.

    Performs up to 3 checks:
    1. App running check (fast) - Ray dashboard API
    2. Endpoint alive check - HEAD request to endpoint
    3. Fields match check - Compare meta.data with GET /flow

    Args:
        meta_url: URL from meta entry (e.g., /my-app/run)
        meta_data_yaml: YAML string from meta.data field
        ray_dashboard: Ray dashboard URL
        ray_serve_address: Ray Serve base URL
        flow_data: Optional pre-fetched flow data from GET /flow
        check_app: Whether to check app status
        check_endpoint: Whether to check endpoint health
        check_fields: Whether to check field completeness

    Returns:
        MetaValidation with all check results
    """
    validation = MetaValidation(url=meta_url)

    # Extract app name from URL path (meta_url is like /app/endpoint)
    # We prepend a fake scheme/host to make urlparse work with paths
    app_name = get_expose_name_from_base_url(f"http://x{meta_url}")
    if not app_name:
        validation.app_running = ValidationResult(
            valid=False,
            message="Cannot extract app name from URL",
            details={"url": meta_url}
        )
        return validation

    # Check 1: App running
    if check_app:
        validation.app_running = await check_app_running(ray_dashboard, app_name)

    # Check 2: Endpoint alive
    if check_endpoint:
        validation.endpoint_alive = await check_endpoint_alive(
            ray_serve_address, meta_url
        )

    # Check 3: Fields match
    if check_fields and flow_data:
        meta_data = parse_meta_data_yaml(meta_data_yaml)
        validation.fields_match = check_fields_match(meta_data, flow_data)

    return validation


async def validate_expose_meta(
    expose_name: str,
    ray_dashboard: str,
    ray_serve_address: str,
    app_server: str,
    auth_cookies: Optional[Dict[str, str]] = None
) -> List[MetaValidation]:
    """
    Validate all meta entries for an expose.

    Fetches current meta from database, flows from GET /flow,
    and validates each entry.

    Args:
        expose_name: Name of the expose to validate
        ray_dashboard: Ray dashboard URL
        ray_serve_address: Ray Serve base URL
        app_server: Kodosumi app server URL (for GET /flow)
        auth_cookies: Authentication cookies

    Returns:
        List of MetaValidation results, one per meta entry
    """
    from kodosumi.service.expose.models import ExposeMeta as ExposeMetaModel

    results = []

    # Get existing meta from database
    existing_metas = await get_existing_meta(expose_name)
    if not existing_metas:
        return results

    # Fetch flows for field comparison
    all_flows = await fetch_registered_flows(app_server, auth_cookies)

    # Build flow lookup by path (meta stores paths like /app/endpoint)
    flow_by_path: Dict[str, dict] = {}
    for flow in all_flows:
        base_url = flow.get("base_url", "")
        url_path = get_path_from_base_url(base_url)
        flow_by_path[url_path] = flow

    # Validate each meta entry
    for meta in existing_metas:
        flow_data = flow_by_path.get(meta.url)

        validation = await validate_meta_entry(
            meta_url=meta.url,
            meta_data_yaml=meta.data,
            ray_dashboard=ray_dashboard,
            ray_serve_address=ray_serve_address,
            flow_data=flow_data
        )
        results.append(validation)

    return results


# =============================================================================
# Step A: Deploy Functions
# =============================================================================

def load_serve_config(config_path: str = RAY_SERVE_CONFIG) -> dict:
    """
    Load and parse serve_config.yaml, create default if missing.

    Args:
        config_path: Path to the serve config YAML file

    Returns:
        Parsed config dictionary with 'applications' key initialized to empty list
    """
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        # Create default config
        config = DEFAULT_SERVE_CONFIG.copy()
        config["applications"] = []
        with open(path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
        return config

    # Load existing config
    with open(path, "r") as f:
        config = yaml.safe_load(f) or {}

    # Ensure applications list exists
    if "applications" not in config:
        config["applications"] = []

    return config


def parse_bootstrap(bootstrap_yaml: str, expose_name: str) -> dict:
    """
    Parse expose bootstrap YAML into Ray Serve application config.

    The bootstrap field contains application-specific config like:
        import_path: mymodule:app
        runtime_env:
          pip: [openai, pydantic]

    This function adds the required name and route_prefix fields.

    Args:
        bootstrap_yaml: YAML string from expose.bootstrap field
        expose_name: Name of the expose (used for app name and route)

    Returns:
        Complete application config dict ready for Ray Serve

    Raises:
        ValueError: If bootstrap is empty or invalid YAML
    """
    if not bootstrap_yaml or not bootstrap_yaml.strip():
        raise ValueError(f"Empty bootstrap for expose '{expose_name}'")

    try:
        app_config = yaml.safe_load(bootstrap_yaml)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in bootstrap for '{expose_name}': {e}")

    if not isinstance(app_config, dict):
        raise ValueError(f"Bootstrap must be a YAML dict for '{expose_name}'")

    # Validate required field
    if "import_path" not in app_config:
        raise ValueError(f"Bootstrap missing 'import_path' for '{expose_name}'")

    # Add/override name and route_prefix
    app_config["name"] = expose_name
    app_config["route_prefix"] = f"/{expose_name}"

    return app_config


async def run_serve_deploy(config_path: str) -> tuple[int, str, str]:
    """
    Run 'serve deploy' command via asyncio subprocess.

    Args:
        config_path: Path to the merged serve config YAML file

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    process = await create_subprocess_exec("deploy", config_path)

    stdout_bytes, stderr_bytes = await process.communicate()

    stdout = stdout_bytes.decode("utf-8") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8") if stderr_bytes else ""

    return (process.returncode or 0, stdout, stderr)


async def _step_deploy(
    progress: BootProgress,
    ray_dashboard: str = "",
    boot_timeout: int = BOOT_HEALTH_TIMEOUT_DEFAULT,
) -> AsyncGenerator[BootMessage, None]:
    """
    Step A: Deploy Ray Serve applications using a sliding window.

    Phase 1 — Parse all bootstraps from expose.db and build the applications list.
    Phase 2 — Sliding window deployment:
      - Submit up to BOOT_BATCH_SIZE apps concurrently to Ray Serve.
      - Poll every BOOT_DEPLOY_POLL_INTERVAL seconds; free a slot only when an
        app reaches a final state (RUNNING, DEPLOY_FAILED, UNHEALTHY).
      - Overall timeout guard: mark remaining apps as timed out and stop.

    The STEP_END message carries both ``deployed_names`` and ``final_statuses``
    so that the caller (_real_boot_process) can skip _step_health_check.

    Yields BootMessage objects for progress tracking.
    """
    # Initialize database
    await db.init_database()

    # Get all exposes
    exposes = await db.get_all_exposes()

    # Filter to enabled exposes with valid bootstrap
    enabled_exposes = [
        e for e in exposes
        if e.get("enabled") and e.get("bootstrap") and e.get("bootstrap").strip()
    ]

    # Setup progress tracking
    progress.current_step = 0
    progress.step_name = "Deploy"
    progress.activities_total = len(enabled_exposes) + 1  # +1 for load config
    progress.activities_done = 0

    yield BootMessage(
        step=BootStep.DEPLOY,
        msg_type=MessageType.STEP_START,
        message="Starting Ray Serve deployment",
        progress=progress
    )

    # Check if there are any exposes to deploy
    if not enabled_exposes:
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.WARNING,
            message="No enabled exposes with bootstrap configuration found",
            progress=progress
        )
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.STEP_END,
            message="Deployment skipped (no applications)",
            progress=progress
        )
        return

    # Load global serve config (base config — reused for every incremental deploy)
    try:
        base_config = load_serve_config()
        progress.activities_done += 1
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.ACTIVITY,
            message="Loading global configuration",
            target="serve_config.yaml",
            result="OK",
            progress=progress
        )
    except Exception as e:
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.ERROR,
            message=f"Failed to load serve config: {e}",
            progress=progress
        )
        return

    # -------------------------------------------------------------------------
    # Phase 1: Parse all bootstraps → build pending list
    # -------------------------------------------------------------------------
    pending: List[dict] = []   # list of app_config dicts waiting to be submitted
    audit = get_audit_logger()

    for expose in enabled_exposes:
        name = expose["name"]
        bootstrap = expose.get("bootstrap", "")

        # DEBUG: Log full expose record (sensitive - bootstrap config, meta)
        audit.debug(f"EXPOSE RECORD {name}: display={expose.get('display')}, network={expose.get('network')}")
        audit.debug(f"  BOOTSTRAP: {bootstrap[:200]}..." if len(bootstrap) > 200 else f"  BOOTSTRAP: {bootstrap}")
        meta = expose.get("meta", "")
        audit.debug(f"  META: {meta[:200]}..." if len(str(meta)) > 200 else f"  META: {meta}")

        try:
            app_config = parse_bootstrap(bootstrap, name)
            pending.append(app_config)

            progress.activities_done += 1
            yield BootMessage(
                step=BootStep.DEPLOY,
                msg_type=MessageType.ACTIVITY,
                message="Prepared deployment config",
                target=name,
                result=f"route=/{name}",
                progress=progress
            )
        except ValueError as e:
            audit.warning(f"DEPLOY SKIP {name} - invalid bootstrap: {e}")
            yield BootMessage(
                step=BootStep.DEPLOY,
                msg_type=MessageType.WARNING,
                message=str(e),
                target=name,
                progress=progress
            )

    if not pending:
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.WARNING,
            message="No valid applications to deploy",
            progress=progress
        )
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.STEP_END,
            message="Deployment skipped (no valid configs)",
            progress=progress
        )
        return

    # -------------------------------------------------------------------------
    # Phase 2: Sliding window deployment
    # -------------------------------------------------------------------------
    # deployed_apps  — name → app_config (already submitted to Ray Serve)
    # final_statuses — name → status dict (reached a terminal Ray state)
    deployed_apps: Dict[str, dict] = {}
    final_statuses: Dict[str, dict] = {}
    deploy_start_time = time.time()

    # Helper: write cumulative config + call serve deploy
    async def _submit_current_window() -> Optional[str]:
        """Write temp YAML for all currently deployed_apps and run serve deploy.

        Returns an error string on failure, None on success.
        """
        window_config = dict(base_config)
        window_config["applications"] = list(deployed_apps.values())
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".yaml",
                delete=False,
                prefix="serve_deploy_"
            ) as f:
                yaml.dump(window_config, f, default_flow_style=False)
                cfg_path = f.name
        except Exception as e:
            return f"Failed to write temp config: {e}"

        try:
            returncode, stdout, stderr = await run_serve_deploy(cfg_path)
        except FileNotFoundError:
            return "'serve' command not found. Is Ray Serve installed?"
        except Exception as e:
            return f"serve deploy raised: {e}"
        finally:
            try:
                Path(cfg_path).unlink()
            except Exception:
                pass

        if returncode != 0:
            return stderr.strip() if stderr else f"Exit code {returncode}"
        return None

    while pending or (len(deployed_apps) > len(final_statuses)):
        # --- Overall timeout guard ---
        elapsed = time.time() - deploy_start_time
        if elapsed >= boot_timeout:
            for name in list(deployed_apps.keys()):
                if name not in final_statuses:
                    final_statuses[name] = {
                        "name": name,
                        "status": "TIMEOUT",
                        "message": f"Timed out after {boot_timeout}s",
                    }
                    yield BootMessage(
                        step=BootStep.DEPLOY,
                        msg_type=MessageType.WARNING,
                        message="Deploy timeout",
                        target=name,
                        result="TIMEOUT",
                        progress=progress
                    )
                    await db.update_expose_state(name, "UNHEALTHY", time.time())
            break

        # --- Fill window up to BOOT_BATCH_SIZE ---
        in_flight = len(deployed_apps) - len(final_statuses)
        while pending and in_flight < BOOT_BATCH_SIZE:
            app_config = pending.pop(0)
            name = app_config["name"]
            deployed_apps[name] = app_config
            in_flight += 1

            # Submit the updated cumulative config to Ray Serve
            error = await _submit_current_window()
            if error:
                yield BootMessage(
                    step=BootStep.DEPLOY,
                    msg_type=MessageType.ERROR,
                    message=f"serve deploy failed: {error}",
                    progress=progress
                )
                return

            yield BootMessage(
                step=BootStep.DEPLOY,
                msg_type=MessageType.ACTIVITY,
                message=f"Submitted to Ray Serve ({len(deployed_apps)} total, {len(pending)} pending)",
                target=name,
                result="submitted",
                progress=progress
            )

        # --- Poll Ray Serve status ---
        if not ray_dashboard:
            # No dashboard URL — fall back to old behaviour: no per-deploy polling.
            # final_statuses will remain empty; _step_health_check handles it.
            break

        await asyncio.sleep(BOOT_DEPLOY_POLL_INTERVAL)

        try:
            current_status = await query_ray_serve_status(ray_dashboard)
        except Exception as e:
            yield BootMessage(
                step=BootStep.DEPLOY,
                msg_type=MessageType.WARNING,
                message=f"Failed to query Ray Serve status: {e}",
                progress=progress
            )
            continue

        for name in list(deployed_apps.keys()):
            if name in final_statuses:
                continue
            if name not in current_status:
                continue

            status = current_status[name]["status"]
            message = current_status[name].get("message", "")

            if is_final_state(status):
                final_statuses[name] = current_status[name]

                db_state = "RUNNING" if status == "RUNNING" else "UNHEALTHY"
                await db.update_expose_state(name, db_state, time.time())

                if status == "RUNNING":
                    yield BootMessage(
                        step=BootStep.DEPLOY,
                        msg_type=MessageType.RESULT,
                        message="Reached final state",
                        target=name,
                        result=status,
                        progress=progress
                    )
                else:
                    yield BootMessage(
                        step=BootStep.DEPLOY,
                        msg_type=MessageType.WARNING,
                        message=message or f"Deployment failed",
                        target=name,
                        result=status,
                        progress=progress
                    )
            else:
                yield BootMessage(
                    step=BootStep.DEPLOY,
                    msg_type=MessageType.ACTIVITY,
                    message=f"Status: {status}",
                    target=name,
                    result=message if message else None,
                    progress=progress
                )

    # Summary
    deployed_names = list(deployed_apps.keys())
    running = sum(1 for s in final_statuses.values() if s["status"] == "RUNNING")
    total = len(deployed_names)

    yield BootMessage(
        step=BootStep.DEPLOY,
        msg_type=MessageType.STEP_END,
        message=f"Deployment complete ({running}/{total} running)" if final_statuses
                else f"Deployment initiated ({total} applications)",
        progress=progress,
        data={
            "deployed_names": deployed_names,
            "final_statuses": final_statuses,
        }
    )


# =============================================================================
# Step B: Health Check Functions
# =============================================================================

def is_final_state(status: str) -> bool:
    """Check if status is a final (non-transitional) state."""
    return status in FINAL_STATES


async def get_current_status(ray_dashboard: str, app_names: Optional[List[str]] = None) -> Dict[str, dict]:
    """
    Get current status of Ray Serve applications (non-blocking).

    Unlike poll_until_final_state(), this returns immediately with current status.
    Use this for status pages or manual checks.

    Args:
        ray_dashboard: Ray dashboard URL
        app_names: Optional list of app names to filter. If None, returns all apps.

    Returns:
        Dict mapping app name to status info:
        {
            "app-name": {
                "name": "app-name",
                "status": "RUNNING",  # or DEPLOYING, UNHEALTHY, etc.
                "message": "",
                "is_final": True
            }
        }
    """
    try:
        all_status = await query_ray_serve_status(ray_dashboard)
    except Exception as e:
        # Return error status for all requested apps
        if app_names:
            return {
                name: {"name": name, "status": "ERROR", "message": str(e), "is_final": True}
                for name in app_names
            }
        return {}

    # Add is_final flag to each status
    for name, info in all_status.items():
        info["is_final"] = is_final_state(info.get("status", ""))

    # Filter if app_names provided
    if app_names:
        return {name: all_status.get(name, {"name": name, "status": "NOT_FOUND", "message": "", "is_final": True})
                for name in app_names}

    return all_status


async def query_ray_serve_status(ray_dashboard: str) -> Dict[str, dict]:
    """
    Query Ray Serve API for all application statuses.

    Args:
        ray_dashboard: Ray dashboard URL (e.g., http://localhost:8265)

    Returns:
        Dict mapping app name to status info:
        {
            "app-name": {
                "name": "app-name",
                "status": "RUNNING",
                "message": ""
            }
        }

    Raises:
        Exception on connection error
    """
    import httpx

    url = f"{ray_dashboard.rstrip('/')}/api/serve/applications/"

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    # Extract applications dict
    applications = data.get("applications", {})

    # Normalize to {name: {status, message, ...}}
    result = {}
    for name, info in applications.items():
        result[name] = {
            "name": name,
            "status": info.get("status", "UNKNOWN"),
            "message": info.get("message", ""),
        }

    return result


async def poll_until_final_state(
    ray_dashboard: str,
    app_names: List[str],
    timeout: int = BOOT_HEALTH_TIMEOUT_DEFAULT,
    interval: int = BOOT_POLL_INTERVAL
) -> Dict[str, dict]:
    """
    Poll Ray Serve API until ALL apps reach a final state.

    Args:
        ray_dashboard: Ray dashboard URL
        app_names: List of application names to monitor
        timeout: Maximum seconds to wait
        interval: Seconds between polls

    Returns:
        Dict mapping app name to final status info

    Raises:
        TimeoutError if any app still transitioning after timeout
    """
    start_time = time.time()
    final_statuses: Dict[str, dict] = {}

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            # Mark remaining apps as timed out
            for name in app_names:
                if name not in final_statuses:
                    final_statuses[name] = {
                        "name": name,
                        "status": "TIMEOUT",
                        "message": f"Timed out after {timeout}s"
                    }
            raise TimeoutError(f"Health check timed out after {timeout}s")

        try:
            current_status = await query_ray_serve_status(ray_dashboard)
        except Exception as e:
            # Connection error - wait and retry
            await asyncio.sleep(interval)
            continue

        # Check each app
        all_final = True
        for name in app_names:
            if name in final_statuses:
                # Already reached final state
                continue

            if name in current_status:
                status = current_status[name]["status"]
                if is_final_state(status):
                    final_statuses[name] = current_status[name]
                else:
                    all_final = False
            else:
                # App not yet visible in Ray Serve
                all_final = False

        if all_final and len(final_statuses) == len(app_names):
            return final_statuses

        await asyncio.sleep(interval)


async def _step_health_check(
    ray_dashboard: str,
    deployed_names: List[str],
    progress: BootProgress,
    boot_timeout: int = BOOT_HEALTH_TIMEOUT_DEFAULT
) -> AsyncGenerator[BootMessage, None]:
    """
    Step B: Wait for all deployed applications to reach a final state.

    Polls Ray Serve API until all apps are RUNNING, UNHEALTHY, DEPLOY_FAILED, etc.
    Each app installs its own virtualenv, so they complete at different times.

    Yields BootMessage objects for progress tracking.
    """
    progress.current_step = 1
    progress.step_name = "Health Check"
    progress.activities_total = len(deployed_names)
    progress.activities_done = 0

    yield BootMessage(
        step=BootStep.HEALTH,
        msg_type=MessageType.STEP_START,
        message=f"Waiting for deployments to complete (timeout: {boot_timeout}s)",
        progress=progress
    )

    if not deployed_names:
        yield BootMessage(
            step=BootStep.HEALTH,
            msg_type=MessageType.WARNING,
            message="No applications to check",
            progress=progress
        )
        yield BootMessage(
            step=BootStep.HEALTH,
            msg_type=MessageType.STEP_END,
            message="Health check skipped (no applications)",
            progress=progress
        )
        return

    # Track which apps have reached final state
    final_statuses: Dict[str, dict] = {}
    start_time = time.time()
    last_status: Dict[str, str] = {}

    while True:
        elapsed = time.time() - start_time

        # Check timeout
        if elapsed >= boot_timeout:
            # Mark remaining apps as timed out
            for name in deployed_names:
                if name not in final_statuses:
                    final_statuses[name] = {
                        "name": name,
                        "status": "TIMEOUT",
                        "message": f"Timed out after {boot_timeout}s"
                    }
                    yield BootMessage(
                        step=BootStep.HEALTH,
                        msg_type=MessageType.WARNING,
                        message=f"Health check timeout",
                        target=name,
                        result="TIMEOUT",
                        progress=progress
                    )
                    # Update database
                    await db.update_expose_state(name, "UNHEALTHY", time.time())
            break

        # Query Ray Serve API
        try:
            current_status = await query_ray_serve_status(ray_dashboard)
        except Exception as e:
            yield BootMessage(
                step=BootStep.HEALTH,
                msg_type=MessageType.WARNING,
                message=f"Failed to query Ray Serve API: {e}",
                progress=progress
            )
            await asyncio.sleep(BOOT_POLL_INTERVAL)
            continue

        # Check each app
        all_final = True
        for name in deployed_names:
            if name in final_statuses:
                continue

            if name in current_status:
                status = current_status[name]["status"]
                message = current_status[name].get("message", "")

                # Only emit message if status changed
                if name not in last_status or last_status[name] != status:
                    last_status[name] = status

                    if is_final_state(status):
                        final_statuses[name] = current_status[name]
                        progress.activities_done += 1

                        yield BootMessage(
                            step=BootStep.HEALTH,
                            msg_type=MessageType.RESULT,
                            message="Reached final state",
                            target=name,
                            result=status,
                            progress=progress
                        )

                        # Update database
                        db_state = "RUNNING" if status == "RUNNING" else "UNHEALTHY"
                        await db.update_expose_state(name, db_state, time.time())

                        if status in ("DEPLOY_FAILED", "UNHEALTHY"):
                            yield BootMessage(
                                step=BootStep.HEALTH,
                                msg_type=MessageType.WARNING,
                                message=message or f"Deployment failed",
                                target=name,
                                progress=progress
                            )
                    else:
                        # Still transitioning
                        all_final = False
                        yield BootMessage(
                            step=BootStep.HEALTH,
                            msg_type=MessageType.ACTIVITY,
                            message=f"Status: {status}",
                            target=name,
                            result=message if message else None,
                            progress=progress
                        )
            else:
                # App not yet visible
                all_final = False

        # Check if all done
        if all_final and len(final_statuses) == len(deployed_names):
            break

        await asyncio.sleep(BOOT_POLL_INTERVAL)

    # Summary
    running = sum(1 for s in final_statuses.values() if s["status"] == "RUNNING")
    failed = len(final_statuses) - running

    yield BootMessage(
        step=BootStep.HEALTH,
        msg_type=MessageType.STEP_END,
        message=f"All deployments complete ({running} running, {failed} failed)",
        progress=progress,
        data={"final_statuses": final_statuses}
    )


# =============================================================================
# Step C: Register Flows Functions
# =============================================================================

@dataclass
class DiscoveredFlow:
    """A flow endpoint discovered from OpenAPI spec."""
    app_name: str
    path: str
    method: str
    summary: str
    description: str
    tags: List[str]
    author: Optional[str] = None
    organization: Optional[str] = None


async def fetch_openapi_spec(ray_serve_address: str, app_name: str) -> tuple[Optional[dict], Optional[str], Optional[str]]:
    """
    Fetch and parse OpenAPI JSON from a deployed app.

    Args:
        ray_serve_address: Ray Serve address (e.g., http://localhost:8005)
        app_name: Name of the application

    Returns:
        Tuple of (spec_dict, url, error_message):
        - (dict, url, None) on success
        - (None, url, error_string) on failure
    """
    import httpx

    url = f"{ray_serve_address.rstrip('/')}/{app_name}/openapi.json"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            if response.status_code == 404:
                return (None, url, f"404 Not Found")
            response.raise_for_status()
            return (response.json(), url, None)
    except httpx.TimeoutException:
        return (None, url, f"Timeout")
    except httpx.ConnectError as e:
        return (None, url, f"Connection error: {e}")
    except Exception as e:
        return (None, url, f"Error: {e}")


def extract_kodosumi_endpoints(openapi_spec: dict, app_name: str) -> List[DiscoveredFlow]:
    """
    Extract endpoints marked with x-kodosumi from OpenAPI spec.

    Looks for endpoints with openapi_extra containing:
    - x-kodosumi: true (the actual marker used by ServeAPI)
    - Also checks legacy variants: x-kodosumi-api, KODOSUMI_API

    Args:
        openapi_spec: Parsed OpenAPI JSON
        app_name: Name of the application

    Returns:
        List of DiscoveredFlow objects
    """
    flows = []
    paths = openapi_spec.get("paths", {})

    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method.lower() not in ("get", "post", "put", "delete", "patch"):
                continue

            # Check for Kodosumi marker in various locations
            is_kodosumi = False

            # Primary marker: x-kodosumi (used by ServeAPI)
            if operation.get(KODOSUMI_API) is True:
                is_kodosumi = True
            # Legacy variants for compatibility
            elif operation.get("x-kodosumi-api") is True:
                is_kodosumi = True
            elif operation.get("KODOSUMI_API") is True:
                is_kodosumi = True

            # Also check in custom extensions
            extensions = operation.get("x-openapi-extra", {})
            if extensions.get("KODOSUMI_API") is True:
                is_kodosumi = True

            if not is_kodosumi:
                continue

            # Build full path including app route prefix
            # OpenAPI paths are relative to the app's mount point
            full_path = f"/{app_name}{path}" if not path.startswith(f"/{app_name}") else path

            # Extract metadata using actual constants from const.py
            flow = DiscoveredFlow(
                app_name=app_name,
                path=full_path,
                method=method.upper(),
                summary=operation.get("summary", ""),
                description=operation.get("description", ""),
                tags=operation.get("tags", []),
                author=operation.get(KODOSUMI_AUTHOR),
                organization=operation.get(KODOSUMI_ORGANIZATION),
            )
            flows.append(flow)

    return flows


async def _step_register_flows(
    ray_serve_address: str,
    app_server: str,
    running_apps: List[str],
    auth_cookies: Optional[Dict[str, str]],
    progress: BootProgress
) -> AsyncGenerator[BootMessage, None]:
    """
    Step C: Register flow endpoints with Kodosumi.

    Uses the /-/routes endpoint which discovers and registers ALL apps at once.
    Then fetches OpenAPI specs for each app to build flow list for step D.

    Yields BootMessage objects for progress tracking.
    """
    import httpx

    progress.current_step = 2
    progress.step_name = "Register Flows"
    progress.activities_total = 2 + len(running_apps)  # register + discover per app
    progress.activities_done = 0

    yield BootMessage(
        step=BootStep.REGISTER,
        msg_type=MessageType.STEP_START,
        message="Registering flow endpoints",
        progress=progress
    )

    if not running_apps:
        yield BootMessage(
            step=BootStep.REGISTER,
            msg_type=MessageType.WARNING,
            message="No running applications to register",
            progress=progress
        )
        yield BootMessage(
            step=BootStep.REGISTER,
            msg_type=MessageType.STEP_END,
            message="Flow registration skipped (no applications)",
            progress=progress
        )
        return

    # Discover flows from each app's OpenAPI for step D (retrieve)
    all_flows: List[DiscoveredFlow] = []

    for app_name in running_apps:
        spec, openapi_url, error = await fetch_openapi_spec(ray_serve_address, app_name)
        progress.activities_done += 1

        if spec is None:
            yield BootMessage(
                step=BootStep.REGISTER,
                msg_type=MessageType.ACTIVITY,
                message=f"OpenAPI not available",
                target=app_name,
                result=error or "skipped",
                progress=progress
            )
            continue

        # Extract flows for step D
        flows = extract_kodosumi_endpoints(spec, app_name)
        if flows:
            all_flows.extend(flows)
            yield BootMessage(
                step=BootStep.REGISTER,
                msg_type=MessageType.ACTIVITY,
                message=f"Discovered {len(flows)} flow endpoint(s)",
                target=app_name,
                result=", ".join(f.path.split("/")[-1] for f in flows),
                progress=progress
            )
        else:
            yield BootMessage(
                step=BootStep.REGISTER,
                msg_type=MessageType.INFO,
                message=f"No endpoints with '{KODOSUMI_API}' marker",
                target=app_name,
                progress=progress
            )

    yield BootMessage(
        step=BootStep.REGISTER,
        msg_type=MessageType.STEP_END,
        message=f"Flow registration complete ({len(all_flows)} discovered)",
        progress=progress,
        data={"discovered_flows": all_flows}
    )


# =============================================================================
# Step D: Retrieve Flows Functions
# =============================================================================

@dataclass
class FlowStatus:
    """Status of a discovered flow endpoint."""
    flow: DiscoveredFlow
    state: str  # alive, dead, not-found, timeout
    response_code: Optional[int]
    checked_at: float


async def check_flow_health(ray_serve_address: str, path: str, timeout: float = 10.0) -> tuple[str, Optional[int]]:
    """
    Send HEAD request to flow endpoint to check health.

    Args:
        ray_serve_address: Ray Serve address (e.g., http://localhost:8005)
        path: Full path to the endpoint (e.g., /my-agent/run)
        timeout: Request timeout in seconds

    Returns:
        Tuple of (state, response_code):
        - ("alive", 200) - Endpoint responding normally
        - ("alive", 405) - Method not allowed but endpoint exists
        - ("not-found", 404) - Endpoint not found
        - ("dead", 5xx) - Server error
        - ("timeout", None) - Request timed out
    """
    import httpx

    url = f"{ray_serve_address.rstrip('/')}{path}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.head(url)

            code = response.status_code

            if code in (200, 204, 405):
                # 405 means endpoint exists but HEAD not allowed - still alive
                return ("alive", code)
            elif code == 404:
                return ("not-found", code)
            elif 500 <= code < 600:
                return ("dead", code)
            else:
                # Other codes (3xx, 4xx) - treat as alive but note the code
                return ("alive", code)

    except httpx.TimeoutException:
        return ("timeout", None)
    except Exception:
        return ("dead", None)


async def check_all_flows(
    ray_serve_address: str,
    flows: List[DiscoveredFlow]
) -> Dict[str, List[FlowStatus]]:
    """
    Check health of all flows in parallel, grouped by app_name.

    Args:
        ray_serve_address: Ray Serve address
        flows: List of discovered flows to check

    Returns:
        Dict mapping app_name to list of FlowStatus objects
    """
    check_time = time.time()
    results: Dict[str, List[FlowStatus]] = {}

    # Create tasks for all flows
    async def check_one(flow: DiscoveredFlow) -> FlowStatus:
        state, code = await check_flow_health(ray_serve_address, flow.path)
        return FlowStatus(
            flow=flow,
            state=state,
            response_code=code,
            checked_at=check_time
        )

    # Run all checks in parallel
    flow_statuses = await asyncio.gather(*[check_one(f) for f in flows])

    # Group by app_name
    for status in flow_statuses:
        app_name = status.flow.app_name
        if app_name not in results:
            results[app_name] = []
        results[app_name].append(status)

    return results


async def _step_retrieve_flows(
    ray_serve_address: str,
    discovered_flows: List[DiscoveredFlow],
    progress: BootProgress
) -> AsyncGenerator[BootMessage, None]:
    """
    Step D: Test each discovered flow endpoint with HEAD requests.

    Verifies that each flow endpoint is responding.

    Yields BootMessage objects for progress tracking.
    """
    progress.current_step = 3
    progress.step_name = "Retrieve Flows"
    progress.activities_total = len(discovered_flows)
    progress.activities_done = 0

    yield BootMessage(
        step=BootStep.RETRIEVE,
        msg_type=MessageType.STEP_START,
        message="Testing flow endpoints",
        progress=progress
    )

    if not discovered_flows:
        yield BootMessage(
            step=BootStep.RETRIEVE,
            msg_type=MessageType.WARNING,
            message="No flows to test",
            progress=progress
        )
        yield BootMessage(
            step=BootStep.RETRIEVE,
            msg_type=MessageType.STEP_END,
            message="Flow retrieval skipped (no flows)",
            progress=progress
        )
        return

    # Group flows by app for reporting
    flows_by_app: Dict[str, List[DiscoveredFlow]] = {}
    for flow in discovered_flows:
        if flow.app_name not in flows_by_app:
            flows_by_app[flow.app_name] = []
        flows_by_app[flow.app_name].append(flow)

    # Report what we're checking
    for app_name, app_flows in flows_by_app.items():
        yield BootMessage(
            step=BootStep.RETRIEVE,
            msg_type=MessageType.ACTIVITY,
            message=f"Checking {len(app_flows)} endpoint(s)",
            target=app_name,
            progress=progress
        )

    # Check all flows
    flow_statuses = await check_all_flows(ray_serve_address, discovered_flows)

    # Report results
    total_alive = 0
    total_dead = 0

    for app_name, statuses in flow_statuses.items():
        for status in statuses:
            progress.activities_done += 1

            state_icon = {
                "alive": "200 OK",
                "not-found": "404",
                "dead": str(status.response_code or "error"),
                "timeout": "timeout"
            }.get(status.state, status.state)

            yield BootMessage(
                step=BootStep.RETRIEVE,
                msg_type=MessageType.RESULT,
                message=f"HEAD {status.flow.path}",
                target=app_name,
                result=f"{state_icon} ({status.state})",
                progress=progress
            )

            if status.state == "alive":
                total_alive += 1
            else:
                total_dead += 1

    yield BootMessage(
        step=BootStep.RETRIEVE,
        msg_type=MessageType.STEP_END,
        message=f"Retrieved {len(discovered_flows)} flow endpoints ({total_alive} alive, {total_dead} dead)",
        progress=progress,
        data={"flow_statuses": flow_statuses}
    )


# =============================================================================
# Step E: Update Meta Functions
# =============================================================================

from kodosumi.service.expose.models import ExposeMeta as ExposeMetaModel, create_meta_template, meta_to_yaml


async def fetch_registered_flows(
    app_server: str,
    auth_cookies: Optional[Dict[str, str]]
) -> List[dict]:
    """
    Fetch all registered flows from GET /flow.

    The API returns a paginated response: {"items": [...], "offset": ...}
    This function fetches all pages and returns a flat list.

    Args:
        app_server: Kodosumi app server URL
        auth_cookies: Authentication cookies

    Returns:
        List of EndpointResponse dicts from the API
    """
    import httpx

    base_url = f"{app_server.rstrip('/')}/flow"
    all_flows = []
    offset = None

    try:
        async with httpx.AsyncClient(timeout=30.0, cookies=auth_cookies) as client:
            while True:
                # Build URL with pagination
                url = base_url
                params = {"pp": 100}  # Fetch 100 at a time
                if offset:
                    params["offset"] = offset

                response = await client.get(url, params=params)
                if response.status_code != 200:
                    break

                data = response.json()
                items = data.get("items", [])
                all_flows.extend(items)

                # Check for more pages
                offset = data.get("offset")
                if not offset:
                    break

    except Exception:
        pass

    return all_flows


def get_expose_name_from_base_url(base_url: str) -> Optional[str]:
    """
    Extract expose name from flow base_url.

    base_url from GET /flow is in the format:
    http://localhost:8005/app-name/endpoint

    We parse the URL and extract the first path segment after the host.

    Args:
        base_url: Full URL like "http://localhost:8005/test-unwrap/"

    Returns:
        Expose name (e.g., "test-unwrap") or None if cannot parse
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(base_url)
        path = parsed.path.strip("/")
        parts = path.split("/")

        if parts and parts[0]:
            return parts[0]
    except Exception:
        pass

    return None


def get_path_from_base_url(base_url: str) -> str:
    """
    Extract path from base_url for storing in meta.

    Args:
        base_url: Full URL like "http://localhost:8005/test-unwrap/run"

    Returns:
        Path like "/test-unwrap/run"
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(base_url)
        return parsed.path or "/"
    except Exception:
        return "/"


async def get_existing_meta(expose_name: str) -> List[ExposeMetaModel]:
    """
    Get existing meta entries for an expose from database.

    Args:
        expose_name: Name of the expose

    Returns:
        List of ExposeMeta objects, empty if none exist
    """
    row = await db.get_expose(expose_name)
    if not row or not row.get("meta"):
        return []

    try:
        meta_list = yaml.safe_load(row["meta"])
        if meta_list:
            return [ExposeMetaModel(**m) for m in meta_list]
    except (yaml.YAMLError, TypeError):
        pass

    return []


def merge_flow_with_meta(
    flow: dict,
    existing_meta: Optional[ExposeMetaModel],
    flow_state: str,
    checked_at: float
) -> ExposeMetaModel:
    """
    Merge a flow with existing meta entry.

    If existing_meta is None, creates a new entry using create_meta_template().
    If existing_meta exists, only updates state and heartbeat, preserving user data.

    Args:
        flow: EndpointResponse dict from GET /flow
        existing_meta: Existing meta entry or None
        flow_state: State from HEAD check (alive, dead, etc.)
        checked_at: Timestamp of the check

    Returns:
        Updated or new ExposeMeta
    """
    # Use base_url and extract path for storage
    base_url = flow.get("base_url", "")
    url_path = get_path_from_base_url(base_url)

    if existing_meta is None:
        # Create new meta using template
        new_meta = create_meta_template(
            url=url_path,
            summary=flow.get("summary"),
            description=flow.get("description"),
            author=flow.get("author"),
            organization=flow.get("organization"),
            tags=flow.get("tags")
        )
        # Set state and heartbeat
        new_meta.state = flow_state
        new_meta.heartbeat = checked_at
        return new_meta
    else:
        # Update only state and heartbeat, preserve user data
        existing_meta.state = flow_state
        existing_meta.heartbeat = checked_at
        return existing_meta


async def _step_update_meta(
    app_server: str,
    auth_cookies: Optional[Dict[str, str]],
    flow_statuses: Dict[str, List[FlowStatus]],
    progress: BootProgress
) -> AsyncGenerator[BootMessage, None]:
    """
    Step E: Update expose metadata in database.

    Fetches flows from GET /flow and merges with existing meta:
    - For new flows: creates meta entry using create_meta_template()
    - For existing flows: only updates state and heartbeat, preserves user data

    Args:
        app_server: Kodosumi app server URL
        auth_cookies: Authentication cookies
        flow_statuses: Dict mapping app_name to list of FlowStatus from Step D
        progress: Progress tracker

    Yields BootMessage objects for progress tracking.
    """
    progress.current_step = 4
    progress.step_name = "Update Meta"
    progress.activities_done = 0

    yield BootMessage(
        step=BootStep.UPDATE,
        msg_type=MessageType.STEP_START,
        message="Updating expose metadata",
        progress=progress
    )

    # Build flow data directly from Step D results (flow_statuses).
    # This avoids the chicken-and-egg problem where GET /flow only returns
    # exposes that already have meta — new agents would never get meta.
    flow_state_lookup: Dict[str, tuple] = {}
    flows_by_expose: Dict[str, List[dict]] = {}

    for app_name, statuses in flow_statuses.items():
        for status in statuses:
            flow = status.flow
            flow_state_lookup[flow.path] = (status.state, status.checked_at)

            # Convert DiscoveredFlow to dict matching EndpointResponse format
            flow_dict = {
                "base_url": flow.path,
                "summary": flow.summary,
                "description": flow.description,
                "tags": flow.tags,
                "author": flow.author,
                "organization": flow.organization,
            }
            if app_name not in flows_by_expose:
                flows_by_expose[app_name] = []
            flows_by_expose[app_name].append(flow_dict)

    all_flow_count = sum(len(v) for v in flows_by_expose.values())

    if not flows_by_expose:
        yield BootMessage(
            step=BootStep.UPDATE,
            msg_type=MessageType.WARNING,
            message="No flows discovered in Step D",
            progress=progress
        )
        yield BootMessage(
            step=BootStep.UPDATE,
            msg_type=MessageType.STEP_END,
            message="Meta update skipped (no flows)",
            progress=progress
        )
        return

    yield BootMessage(
        step=BootStep.UPDATE,
        msg_type=MessageType.RESULT,
        message="Retrieved flows",
        result=f"{all_flow_count} flows",
        progress=progress
    )

    progress.activities_total = len(flows_by_expose) + 1  # +1 for initial fetch
    progress.activities_done = 1  # Already did the fetch

    total_new = 0
    total_updated = 0
    total_alive = 0

    for expose_name, flows in flows_by_expose.items():
        progress.activities_done += 1

        yield BootMessage(
            step=BootStep.UPDATE,
            msg_type=MessageType.ACTIVITY,
            message=f"Processing {len(flows)} flow(s)",
            target=expose_name,
            progress=progress
        )

        try:
            # Get existing meta from database
            existing_metas = await get_existing_meta(expose_name)

            # Build lookup by URL path (meta stores path like /app/endpoint)
            existing_by_path: Dict[str, ExposeMetaModel] = {}
            for meta in existing_metas:
                existing_by_path[meta.url] = meta

            # Merge flows with existing meta
            merged_metas: List[ExposeMetaModel] = []
            new_count = 0
            updated_count = 0

            for flow in flows:
                base_url = flow.get("base_url", "")
                url_path = get_path_from_base_url(base_url)

                # Get state from Step D, default to "alive" if not checked
                # Step D uses the flow.path which should match our url_path
                state, checked_at = flow_state_lookup.get(url_path, ("alive", time.time()))

                # Find existing meta by path
                existing = existing_by_path.get(url_path)

                # Merge
                merged = merge_flow_with_meta(flow, existing, state, checked_at)
                merged_metas.append(merged)

                if existing is None:
                    new_count += 1
                else:
                    updated_count += 1

                if state == "alive":
                    total_alive += 1

            # Save to database
            meta_yaml = meta_to_yaml(merged_metas)
            await db.update_expose_meta(expose_name, meta_yaml)

            total_new += new_count
            total_updated += updated_count

            yield BootMessage(
                step=BootStep.UPDATE,
                msg_type=MessageType.RESULT,
                message="Updated meta",
                target=expose_name,
                result=f"{new_count} new, {updated_count} preserved",
                progress=progress
            )

        except Exception as e:
            yield BootMessage(
                step=BootStep.UPDATE,
                msg_type=MessageType.WARNING,
                message=f"Failed to update meta: {e}",
                target=expose_name,
                progress=progress
            )

    yield BootMessage(
        step=BootStep.UPDATE,
        msg_type=MessageType.STEP_END,
        message=f"Metadata update complete ({total_new} new, {total_updated} preserved, {total_alive} alive)",
        progress=progress
    )


async def start_boot_background(
    ray_dashboard: str,
    ray_serve_address: str,
    app_server: str,
    auth_cookies: Optional[Dict[str, str]] = None,
    force: bool = False,
    owner: str = "system",
    boot_timeout: int = BOOT_HEALTH_TIMEOUT_DEFAULT
) -> bool:
    """
    Start the boot process as a background task.

    Returns True if boot was started, False if already in progress.
    """
    global _boot_task

    # Check if already running (and not forcing)
    if boot_lock.is_locked and not force:
        return False

    async def run_task():
        try:
            async for msg in run_boot_process(
                ray_dashboard=ray_dashboard,
                ray_serve_address=ray_serve_address,
                app_server=app_server,
                boot_timeout=boot_timeout,
                auth_cookies=auth_cookies,
                force=force,
                owner=owner,
            ):
                # Messages are already added to boot_lock in run_boot_process
                # Just consume the generator
                pass
        except asyncio.CancelledError:
            # Task was cancelled - add error message
            msg = BootMessage(
                step=BootStep.ERROR,
                msg_type=MessageType.ERROR,
                message="Boot process was cancelled"
            )
            await boot_lock.add_message(msg)
        except Exception as e:
            # Unexpected error
            msg = BootMessage(
                step=BootStep.ERROR,
                msg_type=MessageType.ERROR,
                message=f"Boot failed: {str(e)}"
            )
            await boot_lock.add_message(msg)
        finally:
            # Always ensure lock is released when task completes
            # This is a safeguard - run_boot_process should release in its finally
            if boot_lock.is_locked:
                boot_lock.release()

    # Start background task
    _boot_task = asyncio.create_task(run_task())

    # Give it a moment to acquire the lock
    await asyncio.sleep(0.1)

    return boot_lock.is_locked


async def run_boot_process(
    ray_dashboard: str,
    ray_serve_address: str,
    app_server: str,
    auth_cookies: Optional[Dict[str, str]] = None,
    force: bool = False,
    owner: str = "system",
    boot_timeout: int = BOOT_HEALTH_TIMEOUT_DEFAULT
) -> AsyncGenerator[BootMessage, None]:
    """
    Execute the boot process step by step.

    Yields BootMessage objects for each step and activity.

    Args:
        ray_dashboard: Ray dashboard URL (e.g., http://localhost:8265)
        ray_serve_address: Ray Serve address for deployment
        app_server: Kodosumi app server URL
        auth_cookies: Authentication cookies for internal API calls
        force: Force boot even if lock is held
        owner: Lock owner identifier
        boot_timeout: Timeout in seconds for deployment health checks
    """

    # Acquire lock
    if not boot_lock.acquire(owner=owner, force=force):
        msg = BootMessage(
            step=BootStep.ERROR,
            msg_type=MessageType.ERROR,
            message="Boot already in progress. Use force=true to override."
        )
        await boot_lock.add_message(msg)
        yield msg
        return

    progress = BootProgress()
    start_time = time.time()

    # Format start time for display
    start_dt = datetime.fromtimestamp(start_time)
    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Emit start time message
    msg = BootMessage(
        step=BootStep.DEPLOY,
        msg_type=MessageType.INFO,
        message=f"Boot started at {start_str}"
    )
    await boot_lock.add_message(msg)
    yield msg

    try:
        async for msg in _real_boot_process(
            ray_dashboard, ray_serve_address, app_server, auth_cookies, progress, boot_timeout, owner
        ):
            await boot_lock.add_message(msg)
            yield msg
            if msg.msg_type == MessageType.ERROR:
                return

        # Calculate runtime
        end_time = time.time()
        end_dt = datetime.fromtimestamp(end_time)
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        runtime_secs = end_time - start_time
        runtime_str = f"{runtime_secs:.1f}s"

        # Complete
        progress.current_step = progress.total_steps
        msg = BootMessage(
            step=BootStep.COMPLETE,
            msg_type=MessageType.STEP_END,
            message=f"Boot completed at {end_str} (runtime: {runtime_str})",
            progress=progress
        )
        await boot_lock.add_message(msg)
        yield msg

    except Exception as e:
        # Calculate runtime even on failure
        end_time = time.time()
        end_dt = datetime.fromtimestamp(end_time)
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        runtime_secs = end_time - start_time
        runtime_str = f"{runtime_secs:.1f}s"

        msg = BootMessage(
            step=BootStep.ERROR,
            msg_type=MessageType.ERROR,
            message=f"Boot failed at {end_str} (runtime: {runtime_str}): {str(e)}"
        )
        await boot_lock.add_message(msg)
        yield msg
    finally:
        boot_lock.release()


async def _run_cleanup_shutdown(
    progress: BootProgress,
    app_server: Optional[str] = None,
    auth_cookies: Optional[Dict[str, str]] = None
) -> AsyncGenerator[BootMessage, None]:
    """
    Run serve shutdown to clean up any stale deployments.

    Called when no applications are deployed but we want to ensure
    Ray Serve is in a clean state.

    Args:
        progress: Boot progress tracker
        app_server: Kodosumi app server URL for flow register call
        auth_cookies: Authentication cookies for internal API calls
    """
    yield BootMessage(
        step=BootStep.DEPLOY,
        msg_type=MessageType.ACTIVITY,
        message="Running serve shutdown -y",
        target="serve",
        progress=progress
    )

    try:
        process = await create_subprocess_exec("shutdown", "-y")
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            yield BootMessage(
                step=BootStep.DEPLOY,
                msg_type=MessageType.RESULT,
                message="Cleanup shutdown",
                result="success",
                progress=progress
            )
        else:
            # Non-zero return is ok - might already be shut down
            error_msg = stderr.decode() if stderr else "Unknown error"
            yield BootMessage(
                step=BootStep.DEPLOY,
                msg_type=MessageType.INFO,
                message=f"Shutdown returned: {error_msg}",
                progress=progress
            )

        # Update all exposes to DEAD state
        await db.init_database()
        exposes = await db.get_all_exposes()
        now = time.time()
        for expose in exposes:
            name = expose["name"]
            await db.update_expose_state(name, "DEAD", now)

        if exposes:
            yield BootMessage(
                step=BootStep.UPDATE,
                msg_type=MessageType.INFO,
                message=f"Set {len(exposes)} expose(s) to DEAD state",
                progress=progress
            )

    except Exception as e:
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.WARNING,
            message=f"Cleanup shutdown failed: {str(e)}",
            progress=progress
        )


async def _real_boot_process(
    ray_dashboard: str,
    ray_serve_address: str,
    app_server: str,
    auth_cookies: Optional[Dict[str, str]],
    progress: BootProgress,
    boot_timeout: int = BOOT_HEALTH_TIMEOUT_DEFAULT,
    owner: str = "system"
) -> AsyncGenerator[BootMessage, None]:
    """
    Real boot process implementation.

    Executes steps A through E:
    - A: Deploy (serve deploy)
    - B: Health Check (poll Ray API) - TODO
    - C: Register Flows (OpenAPI discovery) - TODO
    - D: Retrieve Flows (HEAD requests) - TODO
    - E: Update Meta (database update) - TODO
    """
    audit = get_audit_logger()
    boot_start_time = datetime.now()

    # Audit: Boot process started
    audit.info(f"BOOT START by {owner} - ray_dashboard={ray_dashboard}, ray_serve={ray_serve_address}")

    deployed_names: List[str] = []

    # =========================================================================
    # Step A: Deploy (sliding window — also polls until final state)
    # =========================================================================
    step_a_final_statuses: Dict[str, dict] = {}
    async for msg in _step_deploy(progress, ray_dashboard=ray_dashboard, boot_timeout=boot_timeout):
        yield msg
        if msg.msg_type == MessageType.ERROR:
            audit.error(f"BOOT STEP A FAILED - deploy error: {msg.message}")
            return
        # Capture deployed names and any final statuses already known from Step A
        if msg.data and "deployed_names" in msg.data:
            deployed_names = msg.data["deployed_names"]
        if msg.data and "final_statuses" in msg.data:
            step_a_final_statuses = msg.data["final_statuses"]

    # Audit: Step A complete
    audit.info(f"BOOT STEP A - deployed {len(deployed_names)} exposes: {', '.join(deployed_names) if deployed_names else 'none'}")

    # Check if we have any deployed apps to continue with
    if not deployed_names:
        audit.info("BOOT - no applications to deploy, running cleanup")
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.INFO,
            message="No applications deployed, running cleanup shutdown",
            progress=progress
        )

        # Run serve shutdown to ensure no stale deployments
        async for msg in _run_cleanup_shutdown(progress, app_server, auth_cookies):
            yield msg

        audit.info(f"BOOT END by {owner} - cleanup complete (no applications)")
        # Return early - the lock will be released by run_boot_process's finally block
        return

    # =========================================================================
    # Step B: Health Check
    # Skip if Step A already resolved all final statuses via sliding-window polling
    # =========================================================================
    final_statuses: Dict[str, dict] = {}
    if step_a_final_statuses and set(step_a_final_statuses.keys()) >= set(deployed_names):
        # All apps reached a final state during Step A — no need to poll again
        final_statuses = step_a_final_statuses
        audit.info("BOOT STEP B - skipped (all statuses resolved in Step A)")
    else:
        # Either ray_dashboard was not available or some apps were not yet resolved
        remaining = [n for n in deployed_names if n not in step_a_final_statuses]
        async for msg in _step_health_check(ray_dashboard, remaining, progress, boot_timeout):
            yield msg
            if msg.msg_type == MessageType.ERROR:
                audit.error(f"BOOT STEP B FAILED - health check error: {msg.message}")
                return
            # Capture final statuses for subsequent steps
            if msg.data and "final_statuses" in msg.data:
                final_statuses = msg.data["final_statuses"]
        # Merge Step A partial results with Step B results
        final_statuses = {**step_a_final_statuses, **final_statuses}

    # Filter to only running apps for subsequent steps
    running_apps = [name for name, info in final_statuses.items() if info.get("status") == "RUNNING"]
    failed_apps = [name for name, info in final_statuses.items() if info.get("status") != "RUNNING"]

    # Audit: Step B complete with details
    audit.info(f"BOOT STEP B - health check: {len(running_apps)} running, {len(failed_apps)} failed")
    for name, info in final_statuses.items():
        status = info.get("status", "UNKNOWN")
        if status == "RUNNING":
            audit.info(f"  EXPOSE {name} - status={status}")
        else:
            audit.warning(f"  EXPOSE {name} - status={status}")

    if not running_apps:
        audit.warning("BOOT END - no applications running after health check")
        yield BootMessage(
            step=BootStep.HEALTH,
            msg_type=MessageType.WARNING,
            message="No applications running, skipping remaining steps",
            progress=progress
        )
        return

    # =========================================================================
    # Step C: Register Flows
    # =========================================================================
    discovered_flows: List[DiscoveredFlow] = []
    async for msg in _step_register_flows(
        ray_serve_address, app_server, running_apps, auth_cookies, progress
    ):
        yield msg
        if msg.msg_type == MessageType.ERROR:
            audit.error(f"BOOT STEP C FAILED - register flows error: {msg.message}")
            return
        # Capture discovered flows for subsequent steps
        if msg.data and "discovered_flows" in msg.data:
            discovered_flows = msg.data["discovered_flows"]

    # Audit: Step C complete
    audit.info(f"BOOT STEP C - registered {len(discovered_flows)} flows")

    # =========================================================================
    # Step D: Retrieve Flows
    # =========================================================================
    flow_statuses: Dict[str, List[FlowStatus]] = {}
    async for msg in _step_retrieve_flows(ray_serve_address, discovered_flows, progress):
        yield msg
        if msg.msg_type == MessageType.ERROR:
            audit.error(f"BOOT STEP D FAILED - retrieve flows error: {msg.message}")
            return
        # Capture flow statuses for Step E
        if msg.data and "flow_statuses" in msg.data:
            flow_statuses = msg.data["flow_statuses"]

    # Audit: Step D complete with endpoint details
    total_endpoints = sum(len(flows) for flows in flow_statuses.values())
    alive_endpoints = sum(1 for flows in flow_statuses.values() for f in flows if f.state == "alive")
    audit.info(f"BOOT STEP D - retrieved {total_endpoints} endpoints ({alive_endpoints} alive)")
    for expose_name, statuses in flow_statuses.items():
        for status in statuses:
            audit.info(f"  ENDPOINT {expose_name}{status.flow.path} - state={status.state}")

    # =========================================================================
    # Step E: Update Meta
    # =========================================================================
    async for msg in _step_update_meta(app_server, auth_cookies, flow_statuses, progress):
        yield msg
        if msg.msg_type == MessageType.ERROR:
            audit.error(f"BOOT STEP E FAILED - update meta error: {msg.message}")
            return

    # Audit: Boot complete
    boot_duration = (datetime.now() - boot_start_time).total_seconds()
    audit.info(f"BOOT END by {owner} - success in {boot_duration:.1f}s - {len(running_apps)} exposes, {alive_endpoints} endpoints")


async def run_shutdown(
    app_server: Optional[str] = None,
    auth_cookies: Optional[Dict[str, str]] = None,
    owner: str = "system"
) -> AsyncGenerator[BootMessage, None]:
    """
    Execute serve shutdown with streaming output.

    Args:
        app_server: Kodosumi app server URL for flow register call
        auth_cookies: Authentication cookies for internal API calls
        owner: Username or identifier of who initiated the shutdown
    """
    audit = get_audit_logger()
    audit.info(f"SHUTDOWN START by {owner}")

    yield BootMessage(
        step=BootStep.DEPLOY,
        msg_type=MessageType.STEP_START,
        message="Shutting down Ray Serve"
    )

    try:
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.ACTIVITY,
            message="Running serve shutdown -y",
            target="serve"
        )

        process = await create_subprocess_exec("shutdown", "-y")
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            audit.error(f"SHUTDOWN FAILED - {error_msg}")
            yield BootMessage(
                step=BootStep.ERROR,
                msg_type=MessageType.ERROR,
                message=f"Shutdown failed: {error_msg}"
            )
            return

        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.RESULT,
            message="serve shutdown",
            result="success"
        )

        # Update all exposes to DEAD
        yield BootMessage(
            step=BootStep.UPDATE,
            msg_type=MessageType.STEP_START,
            message="Updating expose states"
        )

        await db.init_database()
        exposes = await db.get_all_exposes()
        expose_names = []
        for expose in exposes:
            name = expose["name"]
            expose_names.append(name)
            await db.update_expose_state(name, "DEAD", time.time())
            yield BootMessage(
                step=BootStep.UPDATE,
                msg_type=MessageType.ACTIVITY,
                message="Set state to DEAD",
                target=name
            )

        audit.info(f"SHUTDOWN - set {len(expose_names)} exposes to DEAD: {', '.join(expose_names) if expose_names else 'none'}")

        audit.info(f"SHUTDOWN END by {owner} - success")
        yield BootMessage(
            step=BootStep.COMPLETE,
            msg_type=MessageType.STEP_END,
            message="Shutdown complete"
        )

    except Exception as e:
        audit.error(f"SHUTDOWN END by {owner} - failed: {e}")
        yield BootMessage(
            step=BootStep.ERROR,
            msg_type=MessageType.ERROR,
            message=f"Shutdown failed: {e}"
        )


async def run_refresh_expose(
    expose_name: str,
    ray_dashboard: str,
    ray_serve_address: str,
    app_server: str,
    auth_cookies: Optional[Dict[str, str]] = None,
) -> AsyncGenerator[BootMessage, None]:
    """
    Refresh a single expose by running: disable → boot → enable → boot.

    This effectively removes and re-adds the expose's flows, useful for
    re-syncing an expose after code changes.

    Args:
        expose_name: Name of the expose to refresh
        ray_dashboard: Ray dashboard URL
        ray_serve_address: Ray Serve address
        app_server: Kodosumi app server URL
        auth_cookies: Authentication cookies for internal API calls
    """
    start_time = time.time()
    start_dt = datetime.fromtimestamp(start_time)
    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")

    yield BootMessage(
        step=BootStep.DEPLOY,
        msg_type=MessageType.INFO,
        message=f"Refreshing expose '{expose_name}' at {start_str}"
    )

    try:
        await db.init_database()

        # =========================================================================
        # Step 1: Disable the expose
        # =========================================================================
        yield BootMessage(
            step=BootStep.UPDATE,
            msg_type=MessageType.STEP_START,
            message=f"Step 1/4: Disabling expose '{expose_name}'"
        )

        expose = await db.get_expose(expose_name)
        if not expose:
            yield BootMessage(
                step=BootStep.ERROR,
                msg_type=MessageType.ERROR,
                message=f"Expose '{expose_name}' not found"
            )
            return

        original_enabled = expose.get("enabled", True)
        # Disable the expose using upsert with all fields
        await db.upsert_expose(
            name=expose_name,
            display=expose.get("display"),
            network=expose.get("network"),
            enabled=False,
            state=expose.get("state", "DRAFT"),
            heartbeat=expose.get("heartbeat") or time.time(),
            bootstrap=expose.get("bootstrap"),
            meta=expose.get("meta")
        )

        yield BootMessage(
            step=BootStep.UPDATE,
            msg_type=MessageType.RESULT,
            message="Expose disabled",
            target=expose_name,
            result="enabled=False"
        )

        # =========================================================================
        # Step 2: Run boot process (removes the expose's flows)
        # =========================================================================
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.STEP_START,
            message="Step 2/4: Running boot process (remove flows)"
        )

        async for msg in run_boot_process(
            ray_dashboard=ray_dashboard,
            ray_serve_address=ray_serve_address,
            app_server=app_server,
            auth_cookies=auth_cookies,
            force=True,  # Force to override any existing lock
            owner=f"refresh:{expose_name}",
        ):
            # Pass through messages but prefix them
            msg.message = f"[Boot 1/2] {msg.message}"
            yield msg
            if msg.msg_type == MessageType.ERROR:
                # Restore original state on error
                expose = await db.get_expose(expose_name)
                if expose:
                    await db.upsert_expose(
                        name=expose_name,
                        display=expose.get("display"),
                        network=expose.get("network"),
                        enabled=original_enabled,
                        state=expose.get("state", "DRAFT"),
                        heartbeat=expose.get("heartbeat") or time.time(),
                        bootstrap=expose.get("bootstrap"),
                        meta=expose.get("meta")
                    )
                return

        # =========================================================================
        # Step 3: Enable the expose
        # =========================================================================
        yield BootMessage(
            step=BootStep.UPDATE,
            msg_type=MessageType.STEP_START,
            message=f"Step 3/4: Enabling expose '{expose_name}'"
        )

        # Re-fetch expose and enable it
        expose = await db.get_expose(expose_name)
        if expose:
            await db.upsert_expose(
                name=expose_name,
                display=expose.get("display"),
                network=expose.get("network"),
                enabled=True,
                state=expose.get("state", "DRAFT"),
                heartbeat=expose.get("heartbeat") or time.time(),
                bootstrap=expose.get("bootstrap"),
                meta=expose.get("meta")
            )

        yield BootMessage(
            step=BootStep.UPDATE,
            msg_type=MessageType.RESULT,
            message="Expose enabled",
            target=expose_name,
            result="enabled=True"
        )

        # =========================================================================
        # Step 4: Run boot process again (re-adds the expose's flows)
        # =========================================================================
        yield BootMessage(
            step=BootStep.DEPLOY,
            msg_type=MessageType.STEP_START,
            message="Step 4/4: Running boot process (re-add flows)"
        )

        async for msg in run_boot_process(
            ray_dashboard=ray_dashboard,
            ray_serve_address=ray_serve_address,
            app_server=app_server,
            auth_cookies=auth_cookies,
            force=True,  # Force to override any existing lock
            owner=f"refresh:{expose_name}",
        ):
            # Pass through messages but prefix them
            msg.message = f"[Boot 2/2] {msg.message}"
            yield msg
            if msg.msg_type == MessageType.ERROR:
                return

        # =========================================================================
        # Complete
        # =========================================================================
        end_time = time.time()
        end_dt = datetime.fromtimestamp(end_time)
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        runtime_secs = end_time - start_time
        runtime_str = f"{runtime_secs:.1f}s"

        yield BootMessage(
            step=BootStep.COMPLETE,
            msg_type=MessageType.STEP_END,
            message=f"Refresh complete at {end_str} (runtime: {runtime_str})"
        )

    except Exception as e:
        yield BootMessage(
            step=BootStep.ERROR,
            msg_type=MessageType.ERROR,
            message=f"Refresh failed: {str(e)}"
        )
