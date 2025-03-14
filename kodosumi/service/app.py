import traceback
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Dict, Union

from litestar import Litestar, Request, Response, Router
from litestar.config.cors import CORSConfig
from litestar.contrib.sqlalchemy.plugins import (SQLAlchemyAsyncConfig,
                                                 SQLAlchemyPlugin)
from litestar.datastructures import State
from litestar.exceptions import (ClientException, NotAuthorizedException,
                                 NotFoundException, ValidationException)
from litestar.middleware import DefineMiddleware
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import JsonRenderPlugin, SwaggerRenderPlugin
from litestar.response import Template
from litestar.static_files import create_static_files_router
from litestar.status_codes import (HTTP_409_CONFLICT,
                                   HTTP_500_INTERNAL_SERVER_ERROR)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import kodosumi.core
import kodosumi.endpoint
from kodosumi import helper
from kodosumi.config import InternalSettings
from kodosumi.dtypes import Role, RoleCreate
from kodosumi.log import app_logger, logger
from kodosumi.service.exec import ExecutionControl
from kodosumi.service.flow import FlowControl
from kodosumi.service.jwt import JWTAuthenticationMiddleware
from kodosumi.service.proxy import ProxyControl
from kodosumi.service.role import RoleControl


def app_exception_handler(request: Request, 
                          exc: Exception) -> Union[Template, Response]:
    ret: Dict[str, Any] = {
        "error": exc.__class__.__name__,
        "path": request.url.path,
    }
    if isinstance(exc, (NotFoundException, NotAuthorizedException)):
        ret["detail"] = exc.detail
        ret["status_code"] = exc.status_code
        extra = ""
        meth = logger.warning
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
    meth(f"{ret['path']} {ret['detail']} ({ret['status_code']}){extra}")
    return Response(content=ret, status_code=ret['status_code'])


async def provide_transaction(
        db_session: AsyncSession, state: State) -> AsyncGenerator[AsyncSession, None]:
    # try:
    async with db_session.begin():
        query = select(Role).filter_by(name="admin")
        result = await db_session.execute(query)
        role = result.scalar_one_or_none()
        if role is None: 
            new_role = RoleCreate(
                name="admin",
                email=state["settings"].ADMIN_EMAIL,
                password=state["settings"].ADMIN_PASSWORD
            )
            create_role = Role(**new_role.model_dump())
            db_session.add(create_role)
            await db_session.flush()
            #await transaction.commit()
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
    for source in app.state["settings"].REGISTER_FLOW:
        try:
            await kodosumi.endpoint.register(app.state, source)
        except:
            logger.critical(f"failed to connect {source}")


async def shutdown(app):
    helper.ray_shutdown()


def create_app(**kwargs) -> Litestar:

    settings = InternalSettings(**kwargs)

    db_config = SQLAlchemyAsyncConfig(
        connection_string=settings.ADMIN_DATABASE,
        metadata=kodosumi.dtypes.Base.metadata,
        create_all=True,
        before_send_handler="autocommit",
    )

    app = Litestar(
        cors_config=CORSConfig(allow_origins=settings.CORS_ORIGINS),
        route_handlers=[
            Router(path="/-/", route_handlers=[RoleControl]),
            Router(path="/-/", route_handlers=[FlowControl]),
            Router(path="/", route_handlers=[ProxyControl]),
            Router(path="/-/", route_handlers=[ExecutionControl]),
            create_static_files_router(
                path="/static", 
                directories=[Path(__file__).parent / "static"],
                opt={"no_auth": True}
            )
        ],
        # template_config=TemplateConfig(
        #     directory=Path(__file__).parent / "templates", engine=JinjaTemplateEngine
        # ),
        dependencies={"transaction": provide_transaction},
        plugins=[SQLAlchemyPlugin(db_config)],
        middleware=[
            DefineMiddleware(
                JWTAuthenticationMiddleware, exclude_from_auth_key="no_auth")
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
            "endpoints": {}
        })
    )
    # helper.ray_init()
    app_logger(settings)
    logger.info(f"app server started at {settings.APP_SERVER}")
    logger.debug(f"admin database at {settings.ADMIN_DATABASE}")
    logger.debug(f"screen log level: {settings.APP_STD_LEVEL}, "
                 f"file log level: {settings.APP_LOG_FILE_LEVEL}, "
                 f"uvicorn log level: {settings.UVICORN_LEVEL}")
    return app
