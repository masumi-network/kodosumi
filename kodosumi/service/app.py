import traceback
from collections.abc import AsyncGenerator
from pathlib import Path
from time import time
from typing import Any, Dict, Union

from litestar import Litestar, Request, Response, Router
from litestar.config.cors import CORSConfig
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.contrib.sqlalchemy.plugins import (SQLAlchemyAsyncConfig,
                                                 SQLAlchemyPlugin)
from litestar.datastructures import State
from litestar.exceptions import (ClientException, NotAuthorizedException,
                                 NotFoundException, ValidationException)
from litestar.middleware import DefineMiddleware
from litestar.middleware.base import MiddlewareProtocol
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import JsonRenderPlugin, SwaggerRenderPlugin
from litestar.response import Redirect, Template
from litestar.static_files import create_static_files_router
from litestar.status_codes import (HTTP_409_CONFLICT,
                                   HTTP_500_INTERNAL_SERVER_ERROR)
from litestar.template.config import TemplateConfig
from litestar.types import ASGIApp, Receive, Scope, Send
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import kodosumi.core
import kodosumi.service.endpoint
from kodosumi import helper
from kodosumi.config import InternalSettings
from kodosumi.dtypes import Role, RoleCreate
from kodosumi.log import app_logger, logger
from kodosumi.service.admin.controller import AdminControl
from kodosumi.service.auth import LoginControl
from kodosumi.service.exec import ExecutionControl
from kodosumi.service.flow import FlowControl
from kodosumi.service.jwt import TOKEN_KEY, JWTAuthenticationMiddleware
from kodosumi.service.proxy import ProxyControl
from kodosumi.service.role import RoleControl
from kodosumi.service.inputs.inputs import InputsController
from kodosumi.service.inputs.outputs import OutputsController
from kodosumi.service.inputs.timeline.controller import TimelineController


def app_exception_handler(request: Request, 
                          exc: Exception) -> Union[Template, Response]:
    ret: Dict[str, Any] = {
        "error": exc.__class__.__name__,
        "path": request.url.path,
    }
    exc_info = False
    if isinstance(exc, NotFoundException):
        ret["detail"] = exc.detail
        ret["status_code"] = exc.status_code
        extra = ""
        meth = logger.warning
    elif isinstance(exc, NotAuthorizedException):
        ret["detail"] = exc.detail
        ret["status_code"] = exc.status_code
        extra = ""
        meth = logger.warning
        if helper.wants(request):
            response = Redirect("/")
            response.delete_cookie(key=TOKEN_KEY)
            return response
    elif isinstance(exc, ValidationException):
        ret["detail"] = f"{exc.detail}: {exc.extra}"
        ret["status_code"] = exc.status_code
        extra = f" - {exc.extra}"
        meth = logger.warning
    else:
        ret["detail"] = str(exc)
        ret["status_code"] = getattr(exc,
            "status_code", HTTP_500_INTERNAL_SERVER_ERROR)
        ret["stacktrace"] = traceback.format_exc()
        extra = f" - {ret['stacktrace']}"
        meth = logger.error
        exc_info = True
    meth(f"{ret['path']} {ret['detail']} ({ret['status_code']}){extra}",
         exc_info=exc_info)
    return Response(content=ret, status_code=ret['status_code'])


async def provide_transaction(
        db_session: AsyncSession, 
        state: State) -> AsyncGenerator[AsyncSession, None]:
    async with db_session.begin():
        query = select(Role).filter_by(name="admin")
        result = await db_session.execute(query)
        role = result.scalar_one_or_none()
        if role is None: 
            new_role = RoleCreate(
                name="admin",
                email=state["settings"].ADMIN_EMAIL,
                password=state["settings"].ADMIN_PASSWORD,
                operator=True
            )
            create_role = Role(**new_role.model_dump())
            db_session.add(create_role)
            await db_session.flush()
            logger.info(
                f"created defaultuser {create_role.name} ({create_role.id})")
        try:
            yield db_session
        except IntegrityError as exc:
            raise ClientException(
                status_code=HTTP_409_CONFLICT,
                detail=repr(exc),
            ) from exc

   
async def startup(app: Litestar):
    helper.ray_init()
    await kodosumi.service.endpoint.reload(
        app.state["settings"].REGISTER_FLOW, app.state)

async def shutdown(app):
    helper.ray_shutdown()


class LoggingMiddleware(MiddlewareProtocol):
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):

        t0 = time()
        status = None

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        if scope["type"] == "http":
            req = Request(scope)

        await self.app(scope, receive, send_wrapper)
       
        if scope["type"] == "http":
            req = Request(scope)
            try:
                user = req.user
            except:
                user = "-"
            logger.info(
                f"{req.method} {req.url.path} - {status} "
                f"in {time() - t0:.4f}s ({user})")


def create_app(**kwargs) -> Litestar:

    settings = InternalSettings(**kwargs)

    db_config = SQLAlchemyAsyncConfig(
        connection_string=settings.ADMIN_DATABASE,
        metadata=kodosumi.dtypes.Base.metadata,
        create_all=True,
        before_send_handler="autocommit",
    )

    admin_console = Path(kodosumi.service.admin.__file__).parent.joinpath

    app = Litestar(
        cors_config=CORSConfig(allow_origins=settings.CORS_ORIGINS,
                               allow_credentials=True),
        route_handlers=[
            Router(path="/", route_handlers=[LoginControl]),
            Router(path="/role", route_handlers=[RoleControl]),
            Router(path="/-/", route_handlers=[ProxyControl]),
            Router(path="/admin", route_handlers=[AdminControl]),
            Router(path="/flow", route_handlers=[FlowControl]),
            Router(path="/exec", route_handlers=[ExecutionControl]),
            Router(path="/inputs", route_handlers=[InputsController]),
            Router(path="/outputs", route_handlers=[OutputsController]),
            Router(path="/timeline", route_handlers=[TimelineController]),
            create_static_files_router(
                path="/static", 
                directories=[admin_console("static"),],
                opt={"no_auth": True}
            ),
        ],
        template_config=TemplateConfig(
            directory=admin_console("templates"),
                engine=JinjaTemplateEngine
        ),
        dependencies={"transaction": provide_transaction},
        plugins=[SQLAlchemyPlugin(db_config)],
        middleware=[
            LoggingMiddleware,
            DefineMiddleware(
                JWTAuthenticationMiddleware, exclude_from_auth_key="no_auth"),
        ],
        openapi_config=OpenAPIConfig(
            title="Kodosumi API",
            description="API documentation for the Kodosumi mesh.",
            version=kodosumi.core.__version__,
            render_plugins=[SwaggerRenderPlugin(), 
                            JsonRenderPlugin()]
        ),
        exception_handlers={Exception: app_exception_handler},
        debug=False,  # obsolete with app_exception_handler
        on_startup=[startup],
        on_shutdown=[shutdown],
        state=State({
            "settings": settings,
            "endpoints": {},
            "routing": {}
        })
    )
    app_logger(settings)
    logger.info(f"app server started at {settings.APP_SERVER}")
    logger.info(f"exec source path {settings.EXEC_DIR}")
    logger.debug(f"admin database at {settings.ADMIN_DATABASE}")
    logger.debug(f"screen log level: {settings.APP_STD_LEVEL}, "
                 f"file log level: {settings.APP_LOG_FILE_LEVEL}, "
                 f"uvicorn log level: {settings.UVICORN_LEVEL}")
    return app
