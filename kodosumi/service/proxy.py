from typing import Any, Dict, Optional, Union

import litestar
import ray
from litestar import MediaType, Request, route, get, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException
from litestar.response import Redirect, Response

import kodosumi.service.endpoint as endpoint
from kodosumi import helper
from kodosumi.const import (KODOSUMI_BASE, KODOSUMI_LAUNCH, KODOSUMI_USER,
                            NAMESPACE, KODOSUMI_URL)
from kodosumi.helper import HTTPXClient
from kodosumi.log import logger
from kodosumi.service.inputs.forms import Model


class LockNotFound(Exception):
    
    def __init__(self, 
                 fid: str, 
                 lid: Optional[str] = None):
        self.fid = fid
        self.lid = lid
        if lid:
            self.message = f"Lock {lid} for {fid} not found."
        else:
            self.message = f"Execution {fid} not found."
        super().__init__()
    

def find_lock(fid: str, lid: str):
    try:
        actor = ray.get_actor(fid, namespace=NAMESPACE)
    except:
        raise LockNotFound(fid, None)
    oref = actor.get_locks.remote()
    locks = ray.get(oref)
    if lid not in locks:
        raise LockNotFound(fid, lid)
    return locks.get(lid), actor


def lease(fid: str, lid: str, result: Dict[str, Any]):
    try:
        actor = ray.get_actor(fid, namespace=NAMESPACE)
    except:
        raise LockNotFound(fid, None)
    oref = actor.lease.remote(lid, result)
    locks = ray.get(oref)
    if lid not in locks:
        raise LockNotFound(fid, lid)
    return locks.get(lid)


class ProxyControl(litestar.Controller):

    tags = ["Proxy"]
    include_in_schema = False

    @route("/{path:path}", http_method=["GET", "POST"])
    async def forward(
            self,
            state: State,
            request: Request,
            path: Optional[str] = None) -> Union[Response, Redirect]:
        lookup = f"/-{path}".rstrip("/")
        target = None
        base = None
        for endpoints in endpoint.items(state):
            for ep in endpoints:
                if ep.url == lookup or ep.url == lookup + "/":
                    target = ep.base_url
                    base = ep.source
                    break
        if target is None or base is None:
            raise NotFoundException(path)
        base = base.replace("/openapi.json", "")
        logger.info(f"proxy forwarding {target} with base "
                    f"{KODOSUMI_BASE}={base}, "
                    f"{KODOSUMI_URL}={request.base_url}")
        async with HTTPXClient() as client:
            meth = request.method.lower()
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            request_headers[KODOSUMI_BASE] = base
            request_headers[KODOSUMI_URL] = str(request.base_url)
            host = request.headers.get("host", None)
            body = await request.body()
            request_headers.pop("content-length", None)
            response = await client.request(
                method=meth,
                url=target,
                headers=request_headers,
                content=body,
                params=request.query_params,
                follow_redirects=True,
                timeout=60)
            response_headers = dict(response.headers)
            if host:
                response_headers["host"] = host
            response_headers.pop("content-length", None)
            if response.status_code == 200:
                fid1 = response.headers.get(KODOSUMI_LAUNCH, "")
                if fid1:
                    fid2 = response.json().get("fid", "")
                    if fid1 == fid2:
                        if helper.wants(request, MediaType.HTML):
                           return Redirect(f"/admin/exec/{fid1}")
                        if helper.wants(request, MediaType.TEXT):
                            return Redirect(f"/exec/state/{fid1}")
                        return Redirect(f"/exec/event/{fid1}")
            else:
                logger.error(
                    f"proxy error: {response.status_code} {response.text}")
            response_content = response.content
        return Response(
                content=response_content,
                status_code=response.status_code,
                headers=response_headers)


class LockController(litestar.Controller):

    tags = ["Lock Control"]

    async def _handle(self,
                      fid: str,
                      lid: str,
                      request: Request) -> Response:
        try:
            lock, actor = find_lock(fid, lid)
        except LockNotFound as e:
            raise NotFoundException(e.message) from e
        target = f"{lock['app_url']}/_lock_/{fid}/{lid}"
        logger.info(f"proxy lock {target} with base "
                    f"{KODOSUMI_URL}={request.base_url}")
        async with HTTPXClient() as client:
            meth = request.method.lower()
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            # request_headers[KODOSUMI_BASE] = base
            host = request.headers.get("host", None)
            body = await request.body()
            request_headers.pop("content-length", None)
            response = await client.request(
                method=meth,
                url=target,
                headers=request_headers,
                content=body,
                params=request.query_params,
                follow_redirects=True)
            response_headers = dict(response.headers)
            if host:
                response_headers["host"] = host
            response_headers.pop("content-length", None)
            if response.status_code == 200:
                if request.method == "GET":
                    model = Model.model_validate(response.json())
                    response_content = model.get_model()
                else:
                    response_content = response.json()
                    result = response_content.get("result", None)
                    actor.lease.remote(lid, result)
            else:
                logger.error(
                    f"proxy error: {response.status_code} {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.text)
        return Response(
                content=response_content,
                status_code=response.status_code,
                headers=response_headers)

    @get("/{fid:str}/{lid:str}",
           summary="Retrieve lock",
           description="Get lock input schema.", operation_id="40_get_lock")
    async def get_lock(self,
                   fid: str,
                   lid: str,
                   request: Request) -> Response:
        return await self._handle(fid, lid, request)
    
    @post("/{fid:str}/{lid:str}",
           summary="Provide lock input",
           description="Post lock input and release the lock.", operation_id="41_post_lock")
    async def post_lock(self,
                   fid: str,
                   lid: str,
                   request: Request) -> Response:
        return await self._handle(fid, lid, request)