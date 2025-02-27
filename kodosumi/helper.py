import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from pydantic import SecretStr  
from typing import Literal
from litestar import Request
from litestar import Litestar, MediaType, Request, Response, get

import ray

from kodosumi.config import InternalSettings, Settings
from kodosumi.log import LOG_FORMAT, get_log_level


DB_USER = {
    "admin": "admin",
    "user1": "user1"
}

format_map = {"html": MediaType.HTML, "json": MediaType.JSON}

def wants(request: Request, format: MediaType = MediaType.HTML) -> bool:
    expect = request.query_params.get("format")
    provided_types = [MediaType.JSON.value, MediaType.HTML.value]
    preferred_type = request.accept.best_match(
        provided_types, default=MediaType.TEXT.value)
    if expect:
        return format_map.get(expect, MediaType.JSON) == format.value
    return preferred_type == format.value


def ray_init(
        settings: Optional[Settings]=None, 
        ignore_reinit_error: bool=False):
    if settings is None:
        settings = InternalSettings()
    ray.init(
        address=settings.RAY_SERVER, 
        ignore_reinit_error=ignore_reinit_error, 
        configure_logging=True, 
        logging_format=LOG_FORMAT, 
        log_to_driver=True, 
        logging_level=max(
            logging.INFO, 
            get_log_level(settings.SPOOLER_STD_LEVEL)
        )
    ) 


def ray_shutdown():
    ray.shutdown()


def parse_factory(entry_point: str) -> Callable:
    if ":" in entry_point:
        module_name, obj = entry_point.split(":", 1)
    else:
        *module_name, obj = entry_point.split(".")
        module_name = ".".join(module_name)
    module = __import__(module_name)
    components = module_name.split('.')
    for comp in components[1:]:
        module = getattr(module, comp)
    return getattr(module, obj)


def now():
    return datetime.now(timezone.utc)


def debug():
    import debugpy
    try:
        if not debugpy.is_client_connected():
            debugpy.listen(("localhost", 63256))
            debugpy.wait_for_client()
    except:
        print("error in kodosumi.helper.debug()")
    breakpoint()


class DotDict(dict):

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(f"'DotDict' object has no attribute '{item}'")

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, item):
        try:
            del self[item]
        except KeyError:
            raise AttributeError(f"'DotDict' object has no attribute '{item}'")


async def verify_user(username: str, password: SecretStr) -> str | None:
    plain_text = password.get_secret_value()
    username = username.lower()
    if username in DB_USER and plain_text == DB_USER[username]:
        return username
    return None
