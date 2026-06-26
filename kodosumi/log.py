import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from kodosumi.config import Settings


LOG_FORMAT = "%(levelname)-8s %(message)s"
LOG_FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s - %(message)s"
AUDIT_LOG_FORMAT = "%(asctime)s %(levelname)s - %(message)s"

# Well-known structured fields slog() exposes as explicit kwargs (promoted to
# top-level JSON keys). Arbitrary additional fields are also supported via
# slog(**extra). Documented here as the canonical field set for log queries.
STRUCTURED_FIELDS = ("fid", "agent", "status", "duration_ms", "node")


logger = logging.getLogger("kodo")
audit_logger = logging.getLogger("kodo.audit")


class StructuredFormatter(logging.Formatter):
    """Emit one JSON object per line (newline-delimited JSON).

    Always includes ``ts``, ``level``, ``logger`` and ``event``. Any of the
    optional :data:`STRUCTURED_FIELDS` attached via :func:`slog` are merged in
    when not ``None``. Falls back to the plain message as ``event`` for log
    records emitted by ordinary ``logger.info(...)`` calls, so the format works
    even before a call site has been migrated to ``slog``.
    """

    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
        }
        payload = getattr(record, "_slog", None)
        if payload:
            doc["event"] = payload.get("event") or record.getMessage()
            for key, val in payload.items():
                if key != "event" and val is not None:
                    doc[key] = val
        else:
            # Un-migrated plain logger.info(...) call — use the message verbatim.
            doc["event"] = record.getMessage()
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False)


def slog(
    _logger: logging.Logger,
    level: int,
    event: str,
    *,
    fid: Optional[str] = None,
    agent: Optional[str] = None,
    status: Optional[str] = None,
    duration_ms: Optional[float] = None,
    node: Optional[str] = None,
    exc_info: bool = False,
    **extra,
) -> None:
    """Emit a structured log record.

    Works with both :class:`StructuredFormatter` (fields become JSON keys) and
    plain ``logging.Formatter`` (the human-readable message is ``event``). Pure
    stdlib, no Ray coupling — safe to import anywhere.
    """
    payload = {"event": event}
    for key, val in (
        ("fid", fid),
        ("agent", agent),
        ("status", status),
        ("duration_ms", duration_ms),
        ("node", node),
    ):
        if val is not None:
            payload[key] = val
    payload.update(extra)
    _logger.log(level, event, extra={"_slog": payload}, exc_info=exc_info)


def get_log_level(level: str):
    return getattr(logging, level.upper())


def access_log_level(status, path, enabled, quiet_paths) -> int:
    """Pick the log level for a per-request access-log line (#71).

    Errors (status >= 400) are always WARNING so failures stay visible even when
    access logging is quieted. Quiet path prefixes (high-frequency UI/status
    polls) and the disabled state are demoted to DEBUG. Everything else is INFO.
    """
    if status is not None and status >= 400:
        return logging.WARNING
    if not enabled or any(path.startswith(p) for p in quiet_paths):
        return logging.DEBUG
    return logging.INFO


def _log_setup(settings: Settings, prefix: str):
    global logger
    _log = logging.getLogger("kodo")
    _log.setLevel(logging.DEBUG)
 
    if _log.hasHandlers():
        _log.handlers.clear()

    _log = logger
    _log.propagate = False
    _log.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    std_level = getattr(settings, f"{prefix}_STD_LEVEL")
    ch.setLevel(getattr(logging, std_level.upper()))
    ch_formatter = logging.Formatter(LOG_FORMAT)
    ch.setFormatter(ch_formatter)
    _log.addHandler(ch)

    log_file = getattr(settings, f"{prefix}_LOG_FILE")
    max_bytes = getattr(settings, f"{prefix}_LOG_MAX_BYTES")
    backup_count = getattr(settings, f"{prefix}_LOG_BACKUP_COUNT")
    fh = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    log_file_level = getattr(settings, f"{prefix}_LOG_FILE_LEVEL")

    fh.setLevel(get_log_level(log_file_level))
    if getattr(settings, f"{prefix}_STRUCTURED_LOG", False):
        fh.setFormatter(StructuredFormatter())
    else:
        fh.setFormatter(logging.Formatter(LOG_FILE_FORMAT))
    _log.addHandler(fh)

    return ch, fh


def spooler_logger(settings: Settings):
    _log_setup(settings, "SPOOLER")


def app_logger(settings: Settings):
    ch, fh = _log_setup(settings, "APP")

    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.addHandler(fh)
    uvicorn_logger.addHandler(ch)
    uvicorn_logger.setLevel(settings.UVICORN_LEVEL)

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(60)

    # Setup audit logger
    setup_audit_logger(settings)


def setup_audit_logger(settings: Settings):
    """
    Setup audit logger with rotating file handler.

    Logs boot/deployment events:
    - INFO: who, what (expose names, endpoints), success/failure
    - DEBUG: full expose records with bootstrap, meta, etc.
    """
    global audit_logger
    _audit = logging.getLogger("kodo.audit")
    _audit.setLevel(logging.DEBUG)
    _audit.propagate = False

    if _audit.hasHandlers():
        _audit.handlers.clear()

    # Ensure parent directory exists
    log_path = Path(settings.AUDIT_LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Rotating file handler - DEBUG level captures everything
    rfh = RotatingFileHandler(
        settings.AUDIT_LOG_FILE,
        maxBytes=settings.AUDIT_LOG_MAX_BYTES,
        backupCount=settings.AUDIT_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    rfh.setLevel(logging.DEBUG)
    rfh.setFormatter(logging.Formatter(AUDIT_LOG_FORMAT))
    _audit.addHandler(rfh)

    audit_logger = _audit
    return _audit


def get_audit_logger() -> logging.Logger:
    """Get the audit logger instance."""
    return logging.getLogger("kodo.audit")
