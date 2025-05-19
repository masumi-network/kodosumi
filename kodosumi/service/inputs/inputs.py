import asyncio
import json
import sqlite3
from pathlib import Path
from typing import AsyncGenerator, Optional, List, Union

import litestar
import ray
from httpx import AsyncClient
from litestar import Request, get, post
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import Redirect, ServerSentEvent, Template
from litestar.types import SSEData

from kodosumi import helper
from kodosumi.helper import now
from kodosumi.log import logger
from kodosumi.runner.const import (DB_FILE, EVENT_STATUS, NAMESPACE,
                                   STATUS_FINAL)
from kodosumi.runner.formatter import DefaultFormatter, Formatter
from kodosumi.service.inputs.forms import Model
from kodosumi.service.proxy import KODOSUMI_BASE, KODOSUMI_USER

FORM_TEMPLATE = "form.html"
#STATUS_REDIRECT = "/admin/exec/{fid}"
STATUS_REDIRECT = "/outputs/status/view/{fid}"


class InputsController(litestar.Controller):

    tags = ["Admin Panel"]
    include_in_schema = True

    @get("/-/{path:path}")
    async def get_scheme(self, 
                         path: str, 
                         state: State,
                         request: Request) -> Template:
        schema_url = str(request.base_url).rstrip("/") + f"/-/{path}"
        timeout = state["settings"].PROXY_TIMEOUT
        async with AsyncClient(timeout=timeout) as client:
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            request_headers[KODOSUMI_BASE] = f"/-/{path}"
            host = request.headers.get("host", None)
            response = await client.get(url=schema_url, headers=request_headers)
            response_headers = dict(response.headers)
            if host:
                response_headers["host"] = host
            response_headers.pop("content-length", None)
            if response.status_code == 200:
                model = Model.model_validate(
                    response.json().get("elements", []))
                response_content = model.render()
            else:
                logger.error(
                    f"Get Schema error: {response.status_code} {response.text}")
                response_content = response.text
        response_headers["content-type"] = "text/html"
        return Template(FORM_TEMPLATE, 
                        context={"html": response_content}, 
                        headers=response_headers)

    @post("/-/{path:path}")
    async def post(self, 
                    path: str, 
                    state: State,
                    request: Request) -> Union[Template, Redirect]:
        schema_url = str(request.base_url).rstrip("/") + f"/-/{path}"
        timeout = state["settings"].PROXY_TIMEOUT
        async with AsyncClient(timeout=timeout) as client:
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            request_headers[KODOSUMI_BASE] = f"/-/{path}"
            request_headers.pop("content-length", None)
            host = request.headers.get("host", None)
            data = await request.form()
            if  data.get("__cancel__") == "__cancel__":
                return Redirect("/")
            response = await client.post(
                url=schema_url, headers=request_headers, json=dict(data))
            response_headers = dict(response.headers)
            if host:
                response_headers["host"] = host
            response_headers.pop("content-length", None)
            if response.status_code == 200:
                errors = response.json().get("errors", None)
                result = response.json().get("result", None)
                elements = response.json().get("elements", [])
                if result:
                    return Redirect(STATUS_REDIRECT.format(fid=str(result)))
                model = Model.model_validate(elements, errors=errors)
                model.set_data(dict(data))
                html = model.render()
            else:
                logger.error(
                    f"Get Schema error: {response.status_code} {response.text}")
                html = f"<h1>500 Server Error</h1>"
                try:
                    js = response.json()
                    text = js.get("detail")
                except:
                    text = response.text
                html += f"<pre><code>{text}</code></pre>"
                
        response_headers["content-type"] = "text/html"
        return Template(FORM_TEMPLATE, 
                        context={"html": html}, 
                        status_code=response.status_code,
                        headers=response_headers)

