from typing import Dict, List

import litestar
from litestar import Request, get, post
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException
from litestar.response import Redirect, Template
from sqlalchemy.ext.asyncio import AsyncSession

import kodosumi.service.endpoint as endpoint
from kodosumi.const import TOKEN_KEY, STATUS_TEMPLATE
from kodosumi.dtypes import RoleEdit
from kodosumi.helper import get_health_status
from kodosumi.service.auth import get_user_details
from kodosumi.service.jwt import operator_guard
from kodosumi.service.role import update_role


class AdminControl(litestar.Controller):

    tags = ["Admin Panel"]
    include_in_schema = False

    @get("/")
    async def home(self) -> Redirect:
        return Redirect("/admin/flow")
    
    @get("/flow")
    async def flow(self, state: State) -> Template:
        data = endpoint.find(state)
        return Template("flow.html", context={"items": data})

    def _get_endpoints(self, state: State) -> dict:
        endpoints = endpoint.keys(state)
        registers = state["settings"].REGISTER_FLOW
        return {
            "endpoints": endpoints,
            "registers": registers,     
            "items": sorted(set(endpoints + registers))
        }

    async def _get_template(self, 
                            request: Request, 
                            transaction: AsyncSession, 
                            state: State,
                            **kwargs) -> Template:
        user = await get_user_details(request.user, transaction)
        data = self._get_endpoints(state)
        return Template(
            "routes.html", context={
                **{
                    "endpoints": data.get("endpoints"),
                    "registers": data.get("registers"),
                    "items": data.get("items"),
                    "user": user,
                }, 
                **kwargs,
                **get_health_status()
            }
        )

    @get("/routes")
    async def routes(self, 
                     request: Request, 
                     state: State, 
                     transaction: AsyncSession) -> Template:
        return await self._get_template(request, transaction, state)

    @post("/routes", guards=[operator_guard])
    async def routes_update(self, 
                            request: Request, 
                            state: State, 
                            transaction: AsyncSession) -> Template:
        result = {}
        message: Dict[str, List[str]] = {"settings": [], "routes": []}
        form_data = await request.form()
        routes_text = form_data.get("routes", "")
        new_pwd1 = form_data.get("new_password1", "")
        new_pwd2 = form_data.get("new_password2", "")
        email = form_data.get("email", "")
        if routes_text:
            routes = [line.strip() 
                    for line in routes_text.split("\n") 
                    if line.strip()]
            endpoint.reset(state)
            for url in routes:
                try:
                    ret = await endpoint.register(state, url)
                    result[url] = [r.model_dump() for r in ret]
                except Exception as e:
                    result[url] = str(e)  # type: ignore
            message["routes"].append("Routes refreshed")
        else:
            if new_pwd1 and new_pwd2:
                if new_pwd1 != new_pwd2:
                    message["settings"].append("Passwords do not match")
                else:
                    await update_role(
                        request.user, RoleEdit(password=new_pwd1), transaction)
                    message["settings"].append("Password successfully updated")
            if email:
                await update_role(
                    request.user, RoleEdit(email=email), transaction)
                message["settings"].append("Settings updated")
        return await self._get_template(
            request, transaction, state, routes=result, message=message)

    @get("/logout")
    async def logout(self, request: Request) -> Redirect:
        if request.user:
            response = Redirect("/")
            response.delete_cookie(key=TOKEN_KEY)
            return response
        raise NotAuthorizedException(detail="Invalid name or password")

    @get("/status/view/{fid:str}")
    async def view_status(self, fid: str) -> Template:
        return Template(STATUS_TEMPLATE, context={"fid": fid})

    @get("/timeline/view", include_in_schema=False)
    async def view_timeline(self,
                           state: State,
                           request: Request) -> Template:
        return Template("timeline/timeline.html", context={})

