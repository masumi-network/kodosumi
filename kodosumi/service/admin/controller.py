import litestar
from litestar import Request, get, post
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

    @get("/routes")
    async def routes(self, state: State) -> Template:
        data = sorted(state["endpoints"].keys())
        return Template("routes.html", context={"items": data})

    @post("/routes")
    async def routes_update(self, state: State, request: Request) -> Template:
        form_data = await request.form()
        routes_text = form_data.get("routes", "")
        routes = [line.strip() 
                  for line in routes_text.split("\n") 
                  if line.strip()]
        state["routing"] = {}
        state["endpoints"] = {}
        result = {}
        for url in routes:
            ret = await kodosumi.service.endpoint.register(state, url)
            result[url] = [r.model_dump() for r in ret]
        return Template("routes.html", context={
            "items": sorted(state["endpoints"].keys()),
            "routes": result
        })

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
