import traceback
from typing import Any, Callable, Union, Optional, Type, List, Sequence, Dict, Set
import inspect
from fastapi import FastAPI, Request, Depends, Response
from fastapi.exceptions import ValidationException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute, BaseRoute
from fastapi.datastructures import Default
from fastapi.utils import generate_unique_id

from kodosumi.runner import KODOSUMI_LAUNCH, create_runner
from kodosumi.service.proxy import KODOSUMI_BASE, KODOSUMI_USER
from kodosumi.service.endpoint import KODOSUMI_API

ANNONYMOUS_USER = "_annon_"


def Launch(request: Request,
           entry_point: Union[Callable, str], 
           inputs: Any=None,
           reference: Optional[Callable] = None) -> JSONResponse:
    if reference is None:
        for sf in inspect.stack():
            if getattr(sf.frame.f_globals.get(sf.function), "_kodosumi_", None):
                reference = sf.frame.f_globals.get(sf.function)
                break
    if reference is None:
        extra = {}
    else:
        extra = request.app._method_lookup.get(reference)
    fid, runner = create_runner(
        username=request.state.user, base_url=request.state.prefix, 
        entry_point=entry_point, inputs=inputs, extra=extra)
    runner.run.remote()  # type: ignore
    return JSONResponse(content={"fid": fid}, headers={KODOSUMI_LAUNCH: fid})


class ServeAPI(FastAPI):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_features()
        self._method_lookup = {}
        self._route_lookup = {}
        # import ray.util
        # import kodosumi.runner
        # ray.util.register_serializer(
        #     kodosumi.runner.runner_fid, 
        #     serializer=kodosumi.runner.custom_serializer, 
        #     deserializer=kodosumi.runner.custom_deserializer)
    
    def _process_route(self, method, path, *args, **kwargs):
        entry = kwargs.pop("entry", None)
        openapi_extra = kwargs.get('openapi_extra', {}) or {}
        if entry:
            openapi_extra[KODOSUMI_API] = True
        for field in ("author", "organization", "version"):
            value = kwargs.pop(field, None)
            if value:
                openapi_extra[f"x-{field}"] = value
        kwargs['openapi_extra'] = openapi_extra
        meth_call = getattr(super(), method)
        original_decorator = meth_call(path, *args, **kwargs)
        def wrapper_decorator(func):
            self._method_lookup[func] = kwargs
            self._route_lookup[(method, path)] = func
            func._kodosumi_ = True
            return original_decorator(func)
        return wrapper_decorator
    
    def get(self, *args, **kwargs):
        return self._process_route("get", *args, **kwargs)

    def post(self, *args, **kwargs):
        return self._process_route("post", *args, **kwargs)
    
    def put(self, *args, **kwargs):
        return self._process_route("put", *args, **kwargs)
    
    def delete(self, *args, **kwargs):
        return self._process_route("delete", *args, **kwargs)
    
    def patch(self, *args, **kwargs):
        return self._process_route("patch", *args, **kwargs)
    
    def options(self, *args, **kwargs):
        return self._process_route("options", *args, **kwargs)
    
    def head(self, *args, **kwargs):
        return self._process_route("head", *args, **kwargs)

    def add_features(self):

        @self.middleware("http")
        async def add_custom_method(request: Request, call_next):
            user = request.headers.get(KODOSUMI_USER, ANNONYMOUS_USER)
            prefix_route = request.headers.get(KODOSUMI_BASE, "")
            request.state.user = user
            request.state.prefix = prefix_route
            response = await call_next(request)
            return response

        @self.exception_handler(Exception)
        @self.exception_handler(ValidationException)
        async def generic_exception_handler(request: Request, exc: Exception):
            return HTMLResponse(content=traceback.format_exc(), status_code=500)
