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
    except Exception:
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


def _host_liveness(nodes: list) -> tuple:
    """Group ray.nodes() incarnations by host; return (alive_hosts, dead_hosts).

    ray.nodes() lists every node *incarnation* in the session, including dead
    historical ones from past restarts. A host counts as alive if ANY of its
    incarnations is alive (so a restarted worker reads as 'alive', not 'dead');
    a host is dead only if all of its incarnations are dead.
    """
    alive_by_host: dict = {}
    for n in nodes:
        key = (n.get("NodeManagerAddress") or n.get("NodeName")
               or n.get("NodeID") or id(n))
        alive_by_host[key] = alive_by_host.get(key, False) or bool(n.get("Alive", False))
    alive = sum(1 for ok in alive_by_host.values() if ok)
    dead = sum(1 for ok in alive_by_host.values() if not ok)
    return alive, dead


def compute_health_status(nodes: list, spooler_ok: bool) -> str:
    """Pure function: derive 'pass'/'warn'/'fail' from ray.nodes() + spooler state.

    Hosts are grouped (see _host_liveness) so dead historical incarnations from
    restarts do not count against a host that is actually up.

    - 'fail': spooler down, or zero alive hosts (cluster unreachable).
    - 'warn': at least one host has only dead incarnations (a node went away).
    - 'pass': spooler ok and every known host has a live incarnation.

    A head-only deployment (no separate worker hosts) is 'pass'.
    """
    if not spooler_ok:
        return "fail"
    alive_hosts, dead_hosts = _host_liveness(nodes)
    if alive_hosts == 0:
        return "fail"
    if dead_hosts > 0:
        return "warn"
    return "pass"


def get_health_status() -> dict:
    try:
        actor = ray.get_actor(SPOOLER_NAME, namespace=NAMESPACE)
        oref = actor.get_meta.remote()
        spooler_status = ray.get(oref)
    except Exception:
        spooler_status = {
            "error": "Spooler not found"
        }

    raw_nodes = ray.nodes()
    spooler_ok = ("error" not in spooler_status
                  if isinstance(spooler_status, dict) else True)

    # Group incarnations by host so restarts don't read as dead nodes.
    alive_by_host: dict = {}
    head_hosts: set = set()
    for n in raw_nodes:
        key = (n.get("NodeManagerAddress") or n.get("NodeName")
               or n.get("NodeID") or id(n))
        alive_by_host[key] = alive_by_host.get(key, False) or bool(n.get("Alive", False))
        if "node:__internal_head__" in (n.get("Resources") or {}):
            head_hosts.add(key)

    worker_hosts = [h for h in alive_by_host if h not in head_hosts]
    alive_workers = sum(1 for h in worker_hosts if alive_by_host[h])
    total_workers = len(worker_hosts)
    head_active = any(alive_by_host[h] for h in head_hosts)
    alive_hosts = sum(1 for ok in alive_by_host.values() if ok)
    dead_hosts = sum(1 for ok in alive_by_host.values() if not ok)

    services = {
        "spooler": "active" if spooler_ok else "down",
        "ray_head": "active" if head_active else "down",
        "ray_workers": f"{alive_workers}/{total_workers} active",
    }

    nodes_summary = {"alive": alive_hosts, "dead": dead_hosts}
    status = compute_health_status(raw_nodes, spooler_ok)

    return {
        "kodosumi_version": kodosumi.__version__,
        "python_version": sys.version,
        "ray_version": ray.__version__,
        "ray_status": raw_nodes,
        "spooler_status": spooler_status,
        "services": services,
        "nodes": nodes_summary,
        "status": status,
    }
