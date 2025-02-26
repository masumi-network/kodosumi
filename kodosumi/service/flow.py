from typing import Optional

import litestar
from httpx import AsyncClient
from litestar import Request, get, route, MediaType
from litestar.datastructures import State
from litestar.response import Response, Template, Redirect

from kodosumi import helper
from kodosumi.config import InternalSettings
from kodosumi.log import logger

TIMEOUT: int = 30
FORWARD_USER = "KODO_USER"
RAY_APP_ENDPOINT = "/api/serve/applications/"
OPEN_API_ENTRY = "openapi.json"
DOCS_ENTRY = "docs"

_fields: tuple = ("summary", "description", "tags", "deprecated")


def _trav(root, *keys):
    for k in keys[:-1]:
        if root and k in root:
            root = root[k]
        else:
            return None
    if root and keys[-1] in root:
        return root[keys[-1]]
    return None


class FlowControl(litestar.Controller):

    async def app_info(
            self, 
            state: State,
            request: Request, 
            client: AsyncClient) -> list:
        t0 = helper.now()
        settings: InternalSettings = state["settings"]
        resp = await client.get(f"{settings.RAY_DASHBOARD}{RAY_APP_ENDPOINT}")
        if resp.status_code != 200:
            raise ConnectionError(f"ray connection failed: {resp.text}")
        js = resp.json()
        endpoints: list[dict] = []
        if not js.get("applications"): 
            return endpoints
        host = js["http_options"].get("host", "127.0.0.1")
        port = js["http_options"].get("port",  8000)
        user = request.user.username
        for elem in js.get("applications", {}).values():
            data = {
                k: elem.get(k, None) 
                for k in ("route_prefix", "docs_path", "status", "name")
            }
            data.update({
                k: _trav(elem, "deployed_app_config", "runtime_env",
                         "kodo_extra", k)
                for k in ("author", "organization")
            })
            entry = f"{settings.RAY_HTTP}{data["route_prefix"]}"
            target = f"{data["route_prefix"]}"
            url = f"{target}/{OPEN_API_ENTRY}"
            try:
                logger.debug(f"contacting ray at {url}")
                resp = await client.get(
                    url, headers={FORWARD_USER: user}, timeout=0.5)
                elem = resp.json()["paths"].get("/")
                data["openapi"] = f"{entry}/{OPEN_API_ENTRY}"
                data["docs"] = f"{entry}/{DOCS_ENTRY}"
                if "get" in elem:
                    data.update({k: elem["get"].get(k, None) for k in _fields})
                    data["entry_point"] = entry
                else:
                    data.update({k: None for k in _fields})
                    data["entry_point"] = data["docs"]
                endpoints.append(data)
            except:
                logger.error(f"failed to get {url}: {resp.status_code}")
        state["app_host"] = host
        state["app_port"] = port
        logger.info(f"received ray application info in {helper.now() - t0}")
        return endpoints

    @get("/-/flows")
    async def info(self, state: State, request: Request) -> Response:
        t0 = helper.now()
        async with AsyncClient(timeout=TIMEOUT) as client:
            data = await self.app_info(state, request, client)
        logger.info(f"GET /-/flows in {helper.now() - t0}")
        if helper.wants(request, MediaType.HTML):
            return Template("flows.html", context={"flows": data})
        return Response(content=data)
    
    @route("/{path:path}", 
           http_method=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy(
            self, 
            state: State, 
            request: Request, 
            path: Optional[str] = None) -> Response:
        async with AsyncClient(timeout=TIMEOUT) as client:
            if not (state["app_host"] and state["app_port"]):
                await self.app_info(state, request, client)
            t0 = helper.now()
            url = f"http://{state["app_host"]}:{state["app_port"]}" \
                  + path if path else "/"
            meth = request.method.lower()
            request_headers = dict(request.headers)
            host = request_headers.pop("host")
            request_headers[FORWARD_USER] = request.user.username
            response = await client.request(
                method=meth,
                url=url,
                headers=request_headers,
                content=await request.body(),
                params=request.query_params,
                follow_redirects=True)
            response_headers = dict(response.headers)
            response_headers["host"] = host
            response_headers.pop("content-length", None)
            if response.history:
                for r in response.history:
                    loc = r.headers.get("location", r.url)
                    logger.debug(
                        f"{meth.upper()} {loc} - "
                        f"{r.reason_phrase} ({r.status_code})")
            logger.info(f"{meth.upper()} {url} - "
                        f"{response.reason_phrase} ({response.status_code}) "
                        f"in {helper.now() - t0}")
            fid = response_headers.pop("kodosumi_launch", None)
            if fid:
                return Redirect(f"/-/executions/{fid}")
            return Response(
                    content=response.content, status_code=response.status_code,
                    headers=response_headers)
