import copy
import inspect
import json
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import ValidationException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import kodosumi.service.admin
from kodosumi.const import (KODOSUMI_BASE, KODOSUMI_LAUNCH, KODOSUMI_USER,
                            KODOSUMI_API, KODOSUMI_URL, HEADER_KEY)
from kodosumi.helper import HTTPXClient
from kodosumi.service.inputs.errors import InputsError
from kodosumi.service.inputs.forms import Checkbox, InputFiles, Model
from kodosumi.service.proxy import LockNotFound, find_lock


ANNONYMOUS_USER = "_annon_"

class ServeAPI(FastAPI):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_features()
        self._method_lookup = {}
        self._route_lookup = {}
        self._lock_lookup = {}
        self._lease_lookup = {}
        self._code_lookup = {}

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
            self._code_lookup[func.__code__] = func
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

    def enter(self, path: str, model: Model, *args, **kwargs):
        openapi_extra = kwargs.get('openapi_extra', None) or {}
        openapi_extra[KODOSUMI_API] = True
        for field in ("author", "organization", "version"):
            value = kwargs.pop(field, None)
            if value:
                openapi_extra[f"x-{field}"] = value
        kwargs['openapi_extra'] = openapi_extra

        def _create_get_handler() -> Callable:
            async def get_form_schema() -> Dict[str, Any]:
                return {**kwargs, **{"elements": model.get_model()}}
            return get_form_schema

        def _create_post_handler(func: Callable) -> Callable:
            async def post_form_handler_internal(request: Request):
                try:
                    js_data = await request.json()
                except Exception:
                    js_data = {}
                elements = model.get_model()
                processed_data: Dict[str, Any] = {}
                items = None
                batch_id = None
                for element in model.children:
                    if not hasattr(element, 'name') or element.name is None:
                        continue
                    element_name = element.name
                    submitted_value: Any = None
                    if isinstance(element, InputFiles):
                        upload = js_data.get(element_name)
                        if upload:
                            js_upload = json.loads(upload)
                            items = js_upload["items"]
                            batch_id = js_upload["batchId"]
                            submitted_value = set([
                                i["filename"] for i in items.values()
                            ])
                    elif element_name in js_data:
                        submitted_value = js_data[element_name]
                    elif isinstance(element, Checkbox):
                        submitted_value = False
                    processed_data[element_name] = element.parse_value(
                        submitted_value)

                sig = inspect.signature(func)
                bound_args = sig.bind_partial()
                if 'inputs' in sig.parameters:
                    bound_args.arguments['inputs'] = processed_data
                if 'request' in sig.parameters:
                    bound_args.arguments['request'] = request
                bound_args.apply_defaults()

                try:
                    if inspect.iscoroutinefunction(func):
                        result = await func(*bound_args.args, 
                                            **bound_args.kwargs)
                    else:
                        result = func(*bound_args.args, **bound_args.kwargs)
                except InputsError as user_func_error:
                    user_func_error.errors.setdefault("_global_", [])
                    user_func_error.errors["_global_"].extend(
                        user_func_error.args)
                    return {
                        "errors": user_func_error.errors,
                        "elements": elements
                    }
                except Exception as exc:
                    raise HTTPException(
                        status_code=500, detail=repr(exc)) from exc
                # try:
                if not hasattr(result, "headers"):
                    raise HTTPException(
                        status_code=400, 
                        detail="kodosumi endpoint must return a Launch object "
                        "or raise an InputsError")
                fid = result.headers.get(KODOSUMI_LAUNCH, None)
                if fid:
                    if items and batch_id:
                        headers = {
                            kodosumi.const.HEADER_KEY: str(request.headers.get(
                                kodosumi.const.HEADER_KEY, ""))
                        }
                        url = request.headers[KODOSUMI_URL]
                        url += f"/files/complete/{fid}/{batch_id}/in"
                        async with HTTPXClient() as client:
                            resp = await client.post(
                                url, json=items, cookies=request.cookies,
                                headers=headers)
                            if resp.status_code != 201:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"Failed to upload files: "
                                            f"{resp.text}")
                    return {
                        "result": fid,
                        "elements": elements
                    }
                raise HTTPException(
                    status_code=400, 
                    detail="kodosumi endpoint must return a Launch object "
                    "or raise an InputsError")
            return post_form_handler_internal

        def decorator(user_func: Callable):
            get_handler = _create_get_handler()
            kwargs_copy = copy.deepcopy(kwargs)
            kwargs_copy['openapi_extra'][KODOSUMI_API] = True
            self.add_api_route(
                path, get_handler, methods=["GET"], **kwargs_copy)
            self._method_lookup[user_func] = {
                 'path': path, 
                 'model': model, 
                 'method': 'GET', 
                 **kwargs_copy 
            }
            self._route_lookup[("get", path)] = user_func 
            post_handler = _create_post_handler(user_func)
            self._code_lookup[post_handler.__code__] = user_func
            self._code_lookup[user_func.__code__] = user_func
            kwargs_copy = copy.deepcopy(kwargs)
            kwargs_copy['openapi_extra'][KODOSUMI_API] = False
            self.add_api_route(
                path, post_handler, methods=["POST"], **kwargs_copy)
            self._route_lookup[("post", path)] = user_func 
            return user_func 
        return decorator

    def lock(self, name: str, **kwargs):
        def wrapper_decorator(func):
            self._lock_lookup[name] = func
            return func
        return wrapper_decorator

    def lease(self, name: str, **kwargs):
        def wrapper_decorator(func):
            self._lease_lookup[name] = func
            return func
        return wrapper_decorator

    def add_features(self):
        app_instance = self
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

        async def _get_model(request: Request, 
                             fid: str, 
                             lid: str) -> Tuple[Dict, Model]:
            try:
                lock, _ = find_lock(fid, lid)
            except LockNotFound as e:
                raise HTTPException(404, e.message) from e
            if lock["result"] is not None:
                raise HTTPException(
                    status_code=404, detail=f"Lock {lid} for {fid} released.")

            get_method = self._lock_lookup[lock["name"]]
            sig = inspect.signature(get_method)
            bound_args = sig.bind_partial()
            if 'data' in sig.parameters:
                bound_args.arguments['data'] = lock.get("data", None)
            if 'request' in sig.parameters:
                bound_args.arguments['request'] = request
            bound_args.apply_defaults()
            if inspect.iscoroutinefunction(get_method):
                model = await get_method(*bound_args.args, **bound_args.kwargs)
            else:
                model = get_method(*bound_args.args, **bound_args.kwargs)
            return lock, model
            
        async def lock_get_handler(request: Request, 
                                   fid: str, 
                                   lid: str) -> Union[List, Dict]:
            _, model = await _get_model(request, fid, lid)
            return model.get_model()
        
        async def lock_post_handler(request: Request, 
                           fid: str, 
                           lid: str) -> Union[List, Dict]:
            lock, model = await _get_model(request, fid, lid)
            if lock["name"] in self._lease_lookup:
                post_method = self._lease_lookup[lock["name"]]
            else:
                post_method = None
            js_data = await request.json()
            elements = model.get_model()
            processed_data: Dict[str, Any] = await request.json()
            for element in model.children:
                if not hasattr(element, 'name') or element.name is None:
                    continue
                element_name = element.name
                submitted_value: Any = None
                if element_name in js_data:
                    submitted_value = js_data[element_name]
                elif isinstance(element, Checkbox):
                    submitted_value = False
                else:
                    submitted_value = None
                processed_data[element_name] = element.parse_value(
                    submitted_value)
            if post_method is None:
                return {
                    "result": processed_data,
                    "elements": elements
                }
            sig = inspect.signature(post_method)
            bound_args = sig.bind_partial()
            if 'data' in sig.parameters:
                bound_args.arguments['data'] = lock.get("data", None)
            if 'inputs' in sig.parameters:
                bound_args.arguments['inputs'] = processed_data
            if 'request' in sig.parameters:
                bound_args.arguments['request'] = request
            bound_args.apply_defaults()
            try:
                if inspect.iscoroutinefunction(post_method):
                    result = await post_method(*bound_args.args, 
                                               **bound_args.kwargs)
                else:
                    result = post_method(*bound_args.args, 
                                         **bound_args.kwargs)
                return {
                    "result": result,
                    "elements": elements
                }
            except InputsError as user_func_error:
                user_func_error.errors.setdefault("_global_", [])
                user_func_error.errors["_global_"].extend(
                    user_func_error.args)
                return {
                    "errors": user_func_error.errors,
                    "elements": elements
                }
            except Exception as user_func_exc:
                raise HTTPException(
                    status_code=500, detail=traceback.format_exc())

        self.add_api_route(
            "/_lock_/{fid:str}/{lid:str}", lock_get_handler, 
            methods=["GET"])
        self.add_api_route(
            "/_lock_/{fid:str}/{lid:str}", lock_post_handler, 
            methods=["POST"])
        
def _static(path):
    return ":/static" + path

class Templates(Jinja2Templates):
    def __init__(self, *args, **kwargs):
        main_dir = Path(
            kodosumi.service.admin.__file__).parent.joinpath("templates")
        if "directory" not in kwargs:
            kwargs["directory"] = []
        else:
            if not isinstance(kwargs["directory"], list):
                kwargs["directory"] = [kwargs["directory"]]
        kwargs["directory"].insert(0, main_dir)
        super().__init__(*args, **kwargs)
        self.env.globals['static'] = _static

