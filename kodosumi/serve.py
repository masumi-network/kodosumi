import inspect
import traceback
from pathlib import Path
from typing import Any, Callable, Dict
import copy

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import ValidationException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import kodosumi.service.admin
from kodosumi.service.endpoint import KODOSUMI_API
from kodosumi.service.inputs.errors import InputsError
from kodosumi.service.inputs.forms import Checkbox, Model
from kodosumi.service.proxy import KODOSUMI_BASE, KODOSUMI_USER
from kodosumi.runner.const import KODOSUMI_LAUNCH


ANNONYMOUS_USER = "_annon_"

class ServeAPI(FastAPI):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_features()
        self._method_lookup = {}
        self._route_lookup = {}
    
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
                # from kodosumi.helper import debug
                # debug()
                # breakpoint()
                return {**kwargs, **{"elements": model.get_model()}}
            return get_form_schema

        def _create_post_handler(func: Callable) -> Callable:
            async def post_form_handler_internal(request: Request):
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

                sig = inspect.signature(func)
                bound_args = sig.bind_partial()
                if 'inputs' in sig.parameters:
                    bound_args.arguments['inputs'] = processed_data
                if 'request' in sig.parameters:
                    bound_args.arguments['request'] = request
                bound_args.apply_defaults()

                try:
                    if inspect.iscoroutinefunction(func):
                        # result = await func(request, processed_data)
                        result = await func(*bound_args.args, 
                                            **bound_args.kwargs)

                    else:
                        result = func(*bound_args.args, **bound_args.kwargs)
                    return {
                        "result": result.headers.get(KODOSUMI_LAUNCH, None),
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
            kwargs_copy = copy.deepcopy(kwargs)
            kwargs_copy['openapi_extra'][KODOSUMI_API] = False
            self.add_api_route(
                path, post_handler, methods=["POST"], **kwargs_copy)
            self._route_lookup[("post", path)] = user_func 
            user_func._kodosumi_ = True 
            return user_func 
        return decorator

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
