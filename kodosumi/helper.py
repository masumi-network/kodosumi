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
    def _resolve(d):
        if isinstance(d, BaseModel):
            return {d.__class__.__name__: d.model_dump()}
        elif isinstance(d, (dict, str, int, float, bool)):
            return {d.__class__.__name__: d}
        elif hasattr(d, "__dict__"):
            return {d.__class__.__name__: d.__dict__}
        elif hasattr(d, "__slots__"):
            return {d.__class__.__name__: {
                k: getattr(d, k) for k in d.__slots__}}
        elif isinstance(d, (list, tuple)):
            return {"__list__": [_resolve(item) for item in d]}
        else:
            return {"TypeError": str(d)}
        
    return DynamicModel(_resolve(data)).model_dump_json()
