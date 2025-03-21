import logging

from kodosumi.config import Settings


LOG_FORMAT = "%(levelname)-8s %(message)s"
LOG_FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s - %(message)s"


logger = logging.getLogger("kodo")


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
    fh = logging.FileHandler(log_file, mode="a")
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
