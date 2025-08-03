from typing import Any, Dict, Optional, Union

import litestar
import ray
from httpx import AsyncClient
from litestar import MediaType, Request, route
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException
from litestar.response import Redirect, Response

import kodosumi.service.endpoint as endpoint
from kodosumi import helper
from kodosumi.const import (KODOSUMI_BASE, KODOSUMI_LAUNCH, KODOSUMI_USER, 
                            NAMESPACE)
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

    tags = ["Reverse Proxy"]
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
        timeout = state["settings"].PROXY_TIMEOUT
        async with AsyncClient(timeout=timeout) as client:
            meth = request.method.lower()
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            request_headers[KODOSUMI_BASE] = base
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
                    f"Proxy error: {response.status_code} {response.text}")
            response_content = response.content
        return Response(
                content=response_content,
                status_code=response.status_code,
                headers=response_headers)


class LockController(litestar.Controller):

    @route("/{fid:str}/{lid:str}", http_method=["GET", "POST"])
    async def lock(self,
                   fid: str,
                   lid: str,
                   state: State,
                   request: Request) -> Response:
        try:
            lock, actor = find_lock(fid, lid)
        except LockNotFound as e:
            raise NotFoundException(e.message) from e
        target = lock['base_url'].rstrip('/') + f"/_lock_/{fid}/{lid}"
        timeout = state["settings"].PROXY_TIMEOUT
        async with AsyncClient(timeout=timeout) as client:
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
                logger.error(response.text)
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.text)
        return Response(
                content=response_content,
                status_code=response.status_code,
                headers=response_headers)
