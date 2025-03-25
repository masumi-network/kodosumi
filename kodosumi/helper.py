import logging
import time
from typing import Optional

import ray
from litestar import MediaType, Request
from pydantic import BaseModel

from kodosumi.config import InternalSettings, Settings
from kodosumi.dtypes import DynamicModel
from kodosumi.log import LOG_FORMAT, get_log_level

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
        ignore_reinit_error: bool=True):
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


def debug():
    import debugpy
    try:
        if not debugpy.is_client_connected():
            debugpy.listen(("localhost", 63256))
            debugpy.wait_for_client()
    except:
        print("error in kodosumi.helper.debug()")
    breakpoint()


def now():
    return time.time()


def serialize(data):
    if isinstance(data, BaseModel):
        dump = {data.__class__.__name__: data.model_dump()}
    elif isinstance(data, (dict, str, int, float, bool)):
        dump = {data.__class__.__name__: data}
    elif hasattr(data, "__dict__"):
        dump = {data.__class__.__name__: data.__dict__}
    elif hasattr(data, "__slots__"):
        dump = {data.__class__.__name__: {
            k: getattr(data, k) for k in data.__slots__}}
    else:
        dump = {"TypeError": str(data)}
    return DynamicModel(dump).model_dump_json()
