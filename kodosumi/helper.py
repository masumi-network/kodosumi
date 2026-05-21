import json
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx
import ray
from litestar import MediaType, Request
from pydantic import BaseModel

import kodosumi
from kodosumi.config import InternalSettings, Settings
from kodosumi.const import (KODOSUMI_BASE, KODOSUMI_LAUNCH, KODOSUMI_URL,
                            KODOSUMI_USER, NAMESPACE, SPOOLER_NAME, KODOSUMI_EXTRA)
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


def debug(port: int=63256):
    import debugpy
    try:
        if not debugpy.is_client_connected():
            debugpy.listen(("localhost", port))
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


class HTTPXClient:
    def __init__(self, **kwargs):
        timeout = InternalSettings().PROXY_TIMEOUT
        self.timeout = timeout if timeout is not None else timeout
        self.follow_redirects = True
        self.kwargs = kwargs
        self.client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self) -> httpx.AsyncClient:
        self.client = httpx.AsyncClient(
            timeout=self.timeout, 
            follow_redirects=self.follow_redirects,
            **self.kwargs
        )
        return self.client
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()


@dataclass
class ProxyResponse:
    """Response from a proxied request."""
    status_code: int
    content: bytes
    headers: Dict[str, str]
    json_data: Optional[Dict[str, Any]] = None

    def json(self) -> Dict[str, Any]:
        """Parse response content as JSON."""
        if self.json_data is not None:
            return self.json_data
        self.json_data = json.loads(self.content)
        return self.json_data


@dataclass
class ProxyRequest:
    """
    Configuration for proxying a request to a Ray Serve endpoint.

    This provides a consistent interface for forwarding requests,
    used by both ProxyControl and Sumi endpoints.
    """
    target_url: str
    method: str = "POST"
    user: str = ""
    base: str = ""
    app_url: str = ""
    body: Optional[bytes] = None
    json_body: Optional[Dict[str, Any]] = None
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    query_params: Optional[Dict[str, str]] = None
    extra: Optional[Dict[str, Any]] = None
    timeout: float = 60.0  # seconds - default proxy request timeout


async def proxy_forward(config: ProxyRequest) -> ProxyResponse:
    """
    Forward a request to a Ray Serve endpoint.

    Sets the standard Kodosumi headers (KODOSUMI_USER, KODOSUMI_BASE, KODOSUMI_URL)
    and optionally includes extra data as X-Kodosumi_Extra header.

    Args:
        config: ProxyRequest configuration

    Returns:
        ProxyResponse with status, content, headers, and parsed JSON if applicable
    """
    request_headers = dict(config.headers)
    request_headers[KODOSUMI_USER] = config.user
    request_headers[KODOSUMI_BASE] = config.base
    request_headers[KODOSUMI_URL] = config.app_url

    if config.extra:
        request_headers[KODOSUMI_EXTRA] = json.dumps(config.extra)

    # Remove content-length as httpx will set it
    request_headers.pop("content-length", None)

    # Determine content to send
    content = None
    json_data = None
    if config.body is not None:
        content = config.body
    elif config.json_body is not None:
        json_data = config.json_body

    async with HTTPXClient() as client:
        response = await client.request(
            method=config.method.lower(),
            url=config.target_url,
            headers=request_headers,
            content=content,
            json=json_data,
            params=config.query_params,
            cookies=config.cookies,
            follow_redirects=True,
            timeout=config.timeout,
        )

        response_headers = dict(response.headers)
        response_headers.pop("content-length", None)

        return ProxyResponse(
            status_code=response.status_code,
            content=response.content,
            headers=response_headers,
        )


def get_health_status() -> dict:
    try:
        actor = ray.get_actor(SPOOLER_NAME, namespace=NAMESPACE)
        oref = actor.get_meta.remote()
        spooler_status = ray.get(oref)
    except:
        spooler_status = {
            "error": "Spooler not found"
        }
    return {
        "kodosumi_version": kodosumi.__version__,
        "python_version": sys.version,
        "ray_version": ray.__version__,
        "ray_status": ray.nodes(),
        "spooler_status": spooler_status
    }
    # return {
    #     "kodosumi_version": None,
    #     "python_version": None,
    #     "ray_version": None,
    #     "ray_status": [],
    #     "spooler_status": None
    # }
