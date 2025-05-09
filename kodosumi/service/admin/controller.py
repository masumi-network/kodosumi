from typing import Any, Dict
import litestar
from litestar import Request, get, post
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException
from litestar.response import Redirect, Template

import kodosumi.service.endpoint
from kodosumi.service.auth import TOKEN_KEY
from kodosumi.service.jwt import operator_guard


class AdminControl(litestar.Controller):

    tags = ["Admin Panel"]
    include_in_schema = False

    @get("/")
    async def home(self) -> Redirect:
        return Redirect("/admin/flow")
    
    @get("/flow")
    async def flow(self, state: State) -> Template:
        data = kodosumi.service.endpoint.get_endpoints(state)
        return Template("flow.html", context={"items": data})

    def _get_endpoints(self, state: State) -> dict:
        endpoints = sorted(state["endpoints"].keys())
        registers = state["settings"].REGISTER_FLOW
        return {
            "endpoints": endpoints,
            "registers": registers,     
            "items": sorted(set(endpoints + registers))
        }

    @get("/routes")
    async def routes(self, state: State) -> Template:
        data = self._get_endpoints(state)
        return Template("routes.html", context=data)

    @post("/routes", guards=[operator_guard])
    async def routes_update(self, state: State, request: Request) -> Template:
        form_data = await request.form()
        routes_text = form_data.get("routes", "")
        routes = [line.strip() 
                  for line in routes_text.split("\n") 
                  if line.strip()]
        state["routing"] = {}
        state["endpoints"] = {}
        result: Dict[str, Any] = {}
        for url in routes:
            try:
                ret = await kodosumi.service.endpoint.register(state, url)
                result[url] = [r.model_dump() for r in ret]
            except Exception as e:
                result[url] = str(e)
        return Template("routes.html", context={
            "items": routes,
            "routes": result
        })

    # @get("/exec")
    # async def exec_list(self) -> Template: 
    #     return Template("exec.html", context={})

    # @get("/exec/{fid:str}")
    # async def exec(self, fid: str) -> Template: 
    #     return Template("status.html", context={"fid": fid})

    @get("/logout")
    async def logout(self, request: Request) -> Redirect:
        if request.user:
            response = Redirect("/")
            response.delete_cookie(key=TOKEN_KEY)
            return response
        raise NotAuthorizedException(detail="Invalid name or password")
