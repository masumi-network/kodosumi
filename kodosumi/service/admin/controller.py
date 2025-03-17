import litestar
from litestar import Request, get
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException
from litestar.response import Redirect, Template

import kodosumi.service.endpoint
from kodosumi.service.auth import TOKEN_KEY


class AdminControl(litestar.Controller):

    @get("/")
    async def home(self) -> Redirect:
        return Redirect("/admin/flow")
    
    @get("/flow")
    async def flow(self, state: State) -> Template:
        data = kodosumi.service.endpoint.get_endpoints(state)
        return Template("flow.html", context={"items": data})

    @get("/exec")
    async def exec_list(self) -> Template: 
        return Template("exec.html", context={})

    @get("/exec/{fid:str}")
    async def exec(self, fid: str) -> Template: 
        return Template("status.html", context={"fid": fid})

    @get("/logout")
    async def logout(self, request: Request) -> Redirect:
        if request.user:
            response = Redirect("/")
            response.delete_cookie(key=TOKEN_KEY)
            return response
        raise NotAuthorizedException(detail="Invalid name or password")
