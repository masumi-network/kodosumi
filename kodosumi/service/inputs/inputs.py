from typing import Union

import litestar
from kodosumi.helper import HTTPXClient
from litestar import Request, get, post
from litestar.datastructures import State
from litestar.response import Redirect, Template
from litestar.exceptions import NotFoundException

from kodosumi.const import FORM_TEMPLATE, KODOSUMI_USER, STATUS_REDIRECT
from kodosumi.log import logger
from kodosumi.service.inputs.forms import Model
from kodosumi.service.proxy import find_lock, LockNotFound


class InputsController(litestar.Controller):

    tags = ["Admin Panel"]
    include_in_schema = False

    @get("/-/{path:path}")
    async def get_scheme(self, 
                         path: str, 
                         state: State,
                         request: Request) -> Template:
        schema_url = str(request.base_url).rstrip(
            "/") + f"/-/{path.lstrip('/')}"
        async with HTTPXClient() as client:
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
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
                    f"get schema error: {response.status_code} "
                    f"{response.text}")
                response_content = response.text
        response_headers["content-type"] = "text/html"
        return Template(FORM_TEMPLATE, 
                        context={"html": response_content}, 
                        headers=response_headers)

    @post("/-/{path:path}")
    async def post_scheme(self, 
                    path: str, 
                    state: State,
                    request: Request) -> Union[Template, Redirect]:
        schema_url = str(request.base_url).rstrip("/") + f"/-/{path}"
        async with HTTPXClient() as client:
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
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
                    f"post schema error: {response.status_code} "
                    f"{response.text}")
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

    @get("/lock/{fid:str}/{lid:str}")
    async def get_lock_scheme(self,
                              fid: str,
                              lid: str, 
                              state: State,
                              request: Request) -> Template:
        try:
            lock, _ = find_lock(fid, lid)
        except LockNotFound as e:
            raise NotFoundException(e.message) from e
        lock_url = f"{lock['app_url']}/_lock_/{fid}/{lid}"
        async with HTTPXClient() as client:
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            host = request.headers.get("host", None)
            response = await client.get(url=lock_url, headers=request_headers)
            response_headers = dict(response.headers)
            if host:
                response_headers["host"] = host
            response_headers.pop("content-length", None)
            if response.status_code == 200:
                model = Model.model_validate(response.json())
                response_content = model.render()
            else:
                logger.error(
                    f"get lock error: {response.status_code} {response.text}")
                response_content = response.text
        response_headers["content-type"] = "text/html"
        return Template(FORM_TEMPLATE, 
                        context={"html": response_content}, 
                        headers=response_headers)


    @post("/lock/{fid:str}/{lid:str}")
    async def post_lock_scheme(self,
                               fid: str,
                               lid: str, 
                               state: State,
                               request: Request) -> Union[Template, Redirect]:
        try:
            lock, _ = find_lock(fid, lid)
        except LockNotFound as e:
            raise NotFoundException(e.message) from e
        app_url = str(request.base_url)
        lock_url = f"{app_url.rstrip('/')}/lock/{fid}/{lid}"
        async with HTTPXClient() as client:
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            request_headers.pop("content-length", None)
            host = request.headers.get("host", None)
            data = await request.form()
            if  data.get("__cancel__") == "__cancel__":
                return Redirect(STATUS_REDIRECT.format(fid=fid))
            response = await client.post(
                url=lock_url, headers=request_headers, json=dict(data))
            response_headers = dict(response.headers)
            response_headers.pop("content-length", None)
            if response.status_code == 200:
                errors = response.json().get("errors", None)
                result = response.json().get("result", None)
                elements = response.json().get("elements", [])
                if result:
                    return Redirect(STATUS_REDIRECT.format(fid=fid))
                model = Model.model_validate(elements, errors=errors)
                model.set_data(dict(data))
                response_content = model.render()
            else:
                logger.error(
                    f"post lock error: {response.status_code} {response.text}")
                response_content = f"<h1>500 Server Error</h1>"
                try:
                    js = response.json()
                    text = js.get("detail")
                except:
                    text = response.text
                response_content += f"<pre><code>{text}</code></pre>"
        response_headers["content-type"] = "text/html"
        return Template(FORM_TEMPLATE, 
                        context={"html": response_content}, 
                        headers=response_headers)
