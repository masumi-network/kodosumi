import traceback
from typing import Any

from bson.objectid import ObjectId
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse

from kodosumi.runner import NAMESPACE, Runner
from kodosumi.service.flow import FORWARD_USER


def Launch(request, entry_point, inputs: Any=None) -> JSONResponse:
        fid = str(ObjectId())
        actor = Runner.options(  # type: ignore
            namespace=NAMESPACE, 
            name=fid, 
            lifetime="detached").remote()
        extra = {}
        for route in request.app.routes:
            if route.path == "/" and "GET" in route.methods:
                extra = {
                    "name": route.name,
                    "summary": route.summary,
                    "description": route.description,
                    "tags": route.tags,
                    "deprecated": route.deprecated
                }
        actor.run.remote(
            request.state.user, fid, entry_point, inputs, extra)
        return JSONResponse(content={"fid": fid}, 
                                     headers={"kodosumi_launch": fid})

class ServeAPI(FastAPI):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_features()

    def add_features(self):

        @self.middleware("http")
        async def add_custom_method(request: Request, call_next):
            user = request.headers[FORWARD_USER]
            prefix_route = request.base_url.path.rstrip("/")
            request.state.user = user
            request.state.prefix_route = prefix_route           
            response = await call_next(request)
            return response

        @self.exception_handler(Exception)
        @self.exception_handler(RequestValidationError)
        async def generic_exception_handler(request: Request, exc: Exception):
            return HTMLResponse(content=traceback.format_exc(), status_code=500)
