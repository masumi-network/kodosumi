import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from kodosumi.config import Settings


LOG_FORMAT = "%(levelname)-8s %(message)s"
LOG_FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s - %(message)s"
AUDIT_LOG_FORMAT = "%(asctime)s %(levelname)s - %(message)s"


logger = logging.getLogger("kodo")
audit_logger = logging.getLogger("kodo.audit")


def get_log_level(level: str):
    return getattr(logging, level.upper())


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
    fh_formatter = logging.Formatter(LOG_FILE_FORMAT)
    fh.setFormatter(fh_formatter)
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
