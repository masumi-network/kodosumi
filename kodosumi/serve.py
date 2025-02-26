import traceback
from typing import Union

from bson.objectid import ObjectId
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from kodosumi.runner import NAMESPACE, Runner
from kodosumi.service.flow import FORWARD_USER


class ServeAPI(FastAPI):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_features()

    def add_features(self):

        @self.middleware("http")
        async def add_custom_method(request: Request, call_next):
            async def execute_flow(entry_point, inputs: Union[BaseModel, dict]):
                #helper.debug()
                fid = str(ObjectId())
                actor = Runner.options(  # type: ignore
                    namespace=NAMESPACE, 
                    name=fid, 
                    lifetime="detached").remote()
                # collect openapi specs
                extra = {}
                for route in request.app.routes:
                    # filter: entry points
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
                return fid
            user = request.headers[FORWARD_USER]
            prefix_route = request.base_url.path.rstrip("/")
            request.state.user = user
            request.state.prefix_route = prefix_route           
            request.state.execute_flow = execute_flow 
            response = await call_next(request)
            return response

        @self.exception_handler(Exception)
        @self.exception_handler(RequestValidationError)
        async def generic_exception_handler(request: Request, exc: Exception):
            return HTMLResponse(content=traceback.format_exc(), status_code=500)
