from typing import Annotated, Any, Optional, Union

import litestar
from litestar import Request, Response, get, post, route
from litestar.enums import RequestEncodingType
from litestar.exceptions import NotAuthorizedException
from litestar.params import Body
from litestar.response import Redirect, Template
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kodosumi import helper
from kodosumi.dtypes import Role, RoleLogin
from kodosumi.log import logger
from kodosumi.service.jwt import HEADER_KEY, TOKEN_KEY, encode_jwt_token


class LoginControl(litestar.Controller):

    tags = ["Access"]

    @get("/login", summary="Login",
         description="Login with name and password.", status_code=200, 
         opt={"no_auth": True})
    async def login_role_get(self, 
                             name: str, 
                             password: str, 
                             transaction: AsyncSession) -> Response:
        return await self._get_role(transaction, name, password)

    @post("/login", summary="Login with form",
         description="Login with name and password using form data.", status_code=200, 
         opt={"no_auth": True})
    async def login_role_post(
            self, 
            data: Annotated[
                RoleLogin, Body(media_type=RequestEncodingType.URL_ENCODED)],
            transaction: AsyncSession) -> Response:
        return await self._get_role(
            transaction, data.name, data.password, data.redirect)

    @post("/api/login", summary="Login with JSON",
         description="Login with name and password using JSON body.", status_code=200, 
         opt={"no_auth": True})
    async def login_role_json(
            self, 
            data: Annotated[
                RoleLogin, Body(media_type=RequestEncodingType.JSON)],
            transaction: AsyncSession) -> Response:
        return await self._get_role(
            transaction, data.name, data.password, data.redirect)

    @route("/logout", summary="Logout",
         description="Logout and remove session cookie..", status_code=200, http_method=["GET", "POST"])
    async def get_logout(self, request: Request) -> Response:
        if request.user:
            response = Response(content="")
            response.delete_cookie(key=TOKEN_KEY)
            return response
        raise NotAuthorizedException(detail="Invalid name or password")

    async def _get_role(self, 
                        transaction: AsyncSession,
                        name: str, 
                        password: str,
                        redirect: Optional[str]=None) -> Union[
                            Response, Redirect]:
        query = select(Role).where(Role.name == name)
        result = await transaction.execute(query)
        role = result.scalar_one_or_none()
        if role:
            if role.verify_password(password):
                if role.active:
                    logger.info(f"role {role.name} ({role.id}) logged in")
                    token = encode_jwt_token(role_id=str(role.id))
                    if redirect:
                        response: Any = Redirect(redirect)
                    else:
                        response: Any = Response(content={
                            "name": role.name, 
                            "id": role.id, 
                            HEADER_KEY: token
                    })
                    response.set_cookie(key=TOKEN_KEY, value=token)
                    return response
        raise NotAuthorizedException(detail="Invalid name or password")

    @get("/", summary="Home",
         description="Admin Console Home.", opt={"no_auth": True})
    async def home(self, request: Request) -> Union[Redirect, Template]:
        if TOKEN_KEY in request.cookies:
            return Redirect("/admin/flow")
        if helper.wants(request):
            return Template("login.html")
        raise NotAuthorizedException(detail="Login requited")
    
