import traceback
from pathlib import Path
from typing import Any, Dict, Union

from litestar import Litestar, Request, Response, Router
from litestar.config.cors import CORSConfig
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException, NotFoundException
from litestar.middleware import DefineMiddleware
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import JsonRenderPlugin, SwaggerRenderPlugin
from litestar.response import Template
from litestar.static_files import create_static_files_router
from litestar.template.config import TemplateConfig

import kodosumi.core
from kodosumi import helper
from kodosumi.config import InternalSettings
from kodosumi.log import app_logger, logger
from kodosumi.service.execution import ExecutionControl
from kodosumi.service.flow import FlowControl
from kodosumi.service.jwt import JWTAuthenticationMiddleware
from kodosumi.service.main import MainControl


def app_exception_handler(request: Request, 
                          exc: Exception) -> Union[Template, Response]:
    ret: Dict[str, Any] = {
        "error": "server error",
        "path": request.url.path,
    }
    if isinstance(exc, NotAuthorizedException) and helper.wants(request):
            return Template("login.html")
    if isinstance(exc, (NotFoundException, NotAuthorizedException)):
        ret["detail"] = exc.detail
        ret["status_code"] = exc.status_code
        extra = ""
        meth = logger.warning
    else:
        ret["detail"] = str(exc)
        ret["status_code"] = 500
        ret["stacktrace"] = traceback.format_exc()
        extra = f" - {ret['stacktrace']}"
        meth = logger.error
    meth(f"{ret['path']} {ret['detail']} ({ret['status_code']}){extra}")
    return Response(content=ret, status_code=ret['status_code'])


def create_app(**kwargs) -> Litestar:

    settings = InternalSettings()
    app = Litestar(
        cors_config=CORSConfig(allow_origins=settings.CORS_ORIGINS),
        route_handlers=[
            Router(path="/", route_handlers=[MainControl]),
            Router(path="/", route_handlers=[FlowControl]),
            Router(path="/", route_handlers=[ExecutionControl]),
            create_static_files_router(
                path="/static", 
                directories=[Path(__file__).parent / "static"],
                opt={"no_auth": True}
            )
        ],
        template_config=TemplateConfig(
            directory=Path(__file__).parent / "templates", engine=JinjaTemplateEngine
        ),
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
        state=State({
            "settings": settings,
            "app_host": None,
            "app_port": None
        })
    )
    helper.ray_init()
    app_logger(settings)
    logger.info(f"app server started at {settings.APP_SERVER}")
    logger.debug(f"screen log level: {settings.APP_STD_LEVEL}, "
                 f"file log level: {settings.APP_LOG_FILE_LEVEL}, "
                 f"uvicorn log level: {settings.UVICORN_LEVEL}")
    return app
