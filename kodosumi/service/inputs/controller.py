import litestar
from litestar import Request, get, post, MediaType
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException
from litestar.response import Redirect, Template, Response
from httpx import AsyncClient
import json

import kodosumi.service.endpoint
from kodosumi.service.auth import TOKEN_KEY
from kodosumi.service.jwt import operator_guard
from kodosumi.service.proxy import KODOSUMI_USER, KODOSUMI_BASE, KODOSUMI_LAUNCH, update_links
from kodosumi.log import logger
from kodosumi.service.inputs.forms import Model
from kodosumi import helper


class InputsController(litestar.Controller):

    tags = ["Admin Panel"]
    include_in_schema = False

    @get("/{path:path}")
    async def get_scheme(self, 
                         path: str, 
                         state: State,
                         request: Request) -> Template:
        schema_url = str(request.base_url).rstrip("/") + path
        timeout = state["settings"].PROXY_TIMEOUT
        async with AsyncClient(timeout=timeout) as client:
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            request_headers[KODOSUMI_BASE] = path
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
        return Template("inputs/_master.html", 
                        context={"html": response_content}, 
                        headers=response_headers)

    @post("/{path:path}")
    async def post(self, 
                    path: str, 
                    state: State,
                    request: Request) -> Template:
        schema_url = str(request.base_url).rstrip("/") + path
        timeout = state["settings"].PROXY_TIMEOUT
        async with AsyncClient(timeout=timeout) as client:
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            request_headers[KODOSUMI_BASE] = path
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
                    fid = json.loads(result.get("body")).get("fid", None)
                    return Redirect(f"/admin/exec/{fid}")
                model = Model.model_validate(elements, errors=errors)
                model.set_data(dict(data))
                html = model.render()
            else:
                logger.error(
                    f"Get Schema error: {response.status_code} {response.text}")
                response_content = response.text
        response_headers["content-type"] = "text/html"
        return Template("inputs/_master.html", 
                        context={"html": html}, 
                        status_code=response.status_code,
                        headers=response_headers)
