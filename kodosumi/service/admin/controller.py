import litestar
from litestar import Response, get, Request
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException
from litestar.response import Template, Redirect
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from typing import Union

from kodosumi.service.auth import TOKEN_KEY
import kodosumi.service.endpoint

class AdminControl(litestar.Controller):

    @get("/")
    async def home(self) -> Redirect:
        return Redirect("/admin/flow")
    
    @get("/flow")
    async def flow(self, state: State) -> Template:
        data = kodosumi.service.endpoint.get_endpoints(state)
        return Template("flow.html", context={"items": data})

    @get("/exec")
    async def exec_list(self, state: State) -> Template: 
        return Template("exec.html", context={})

    @get("/exec/{fid:str}")
    async def exec(self, fid: str, state: State) -> Template: 
        return Template("status.html", context={"fid": fid})

    @get("/logout", status_code=200)
    async def logout(self, request: Request) -> Redirect:
        if request.user:
            response = Redirect("/")
            response.delete_cookie(key=TOKEN_KEY)
            return response
        raise NotAuthorizedException(detail="Invalid name or password")
