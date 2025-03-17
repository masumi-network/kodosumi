import uuid

import litestar
from litestar import Request, Response, delete, get, post, put, route
from litestar.response import Template, Redirect
from litestar.enums import RequestEncodingType
from litestar.exceptions import NotAuthorizedException, NotFoundException
from litestar.params import Body
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated, Optional, Union, Any
from kodosumi.dtypes import Role, RoleCreate, RoleEdit, RoleLogin, RoleResponse
from kodosumi.log import logger
from kodosumi.service.jwt import HEADER_KEY, TOKEN_KEY, encode_jwt_token
from kodosumi import helper

class LoginControl(litestar.Controller):

    @get("/login", status_code=200, opt={"no_auth": True})
    async def login_role_get(self, 
                             name: str, 
                             password: str, 
                             transaction: AsyncSession) -> Response:
        return await self._get_role(transaction, name, password)

    @post("/login", status_code=200, opt={"no_auth": True})
    async def login_role_post(
            self, 
            data: Annotated[
                RoleLogin, Body(media_type=RequestEncodingType.URL_ENCODED)],
            transaction: AsyncSession) -> Response:
        return await self._get_role(
            transaction, data.name, data.password, data.redirect)

    @route("/logout", status_code=200, http_method=["GET", "POST"])
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
                        redirect: Optional[str]=None) -> Union[Response,
                                                               Redirect]:
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

    @get("/", opt={"no_auth": True})
    async def home(self, request: Request) -> Union[Redirect, Template]:
        if TOKEN_KEY in request.cookies:
            return Redirect("/admin/flow")
        if helper.wants(request):
            return Template("login.html")
        raise NotAuthorizedException(detail="Login requited")
    
