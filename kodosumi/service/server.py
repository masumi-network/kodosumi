import json
import os
import urllib

import uvicorn

from kodosumi.config import Settings
from kodosumi.log import LOG_FORMAT


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

    uvicorn.run(
        "kodosumi.service.app:create_app",
        host=server.hostname,
        port=server.port,
        reload=settings.APP_RELOAD,
        factory=True,
        log_config=log_config,
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
