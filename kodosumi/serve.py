import traceback
from typing import Any, Callable, Union

from fastapi import FastAPI, Request
from fastapi.exceptions import ValidationException
from fastapi.responses import HTMLResponse, JSONResponse

from kodosumi.runner import KODOSUMI_LAUNCH, create_runner
from kodosumi.service.proxy import KODOSUMI_BASE, KODOSUMI_USER
from kodosumi.service.endpoint import KODOSUMI_API


ANNONYMOUS_USER = "_annon_"


def Launch(request: Request,
           entry_point: Union[Callable, str], 
           inputs: Any=None) -> JSONResponse:
    fid, runner = create_runner(
        username=request.state.user, base_url=request.state.prefix, 
        entry_point=entry_point, inputs=inputs)
    runner.run.remote()  # type: ignore
    return JSONResponse(content={"fid": fid}, headers={KODOSUMI_LAUNCH: fid})


class ServeAPI(FastAPI):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_features()

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
