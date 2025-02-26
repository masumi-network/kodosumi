from typing import Any, Optional, Union

import litestar
from litestar import MediaType, Request, get, route
from litestar.datastructures import State
from litestar.exceptions import NotAuthorizedException
from litestar.response import Redirect, Response, Template
from pydantic import SecretStr

import kodosumi.core
from kodosumi import helper
from kodosumi.dtypes import Login
from kodosumi.log import logger
from kodosumi.service.jwt import (HEADER_KEY, TOKEN_KEY, encode_jwt_token,
                                  parse_token)


class MainControl(litestar.Controller):

    @get("/-/helo", opt={"no_auth": True})
    async def helo(self, state: State, request: Request) -> Response:
        t0 = helper.now()
        try:
            token = parse_token(request)
            username = token.sub
        except:
            username = None
        logger.info(f"GET /-/helo in {helper.now() - t0}")
        return Response(content={
            "name": "kodosumi", 
            "version": kodosumi.core.__version__,
            "username": username
        })

    @get("/")
    async def home(self) -> Redirect:
        return Redirect("/-/flows")

    @route("/-/login", opt={"no_auth": True}, http_method=["GET", "POST"])
    async def login(self, 
                    request: Request,
                    username: Optional[str]=None, 
                    password: Optional[str]=None) -> Union[
                        Redirect, Template, Response]:
        t0 = helper.now()
        form = await request.form()
        if not username or not password:
            username = form.get("username", "")
            password = form.get("password", "")
        if not username or not password:
            try:
                js = await request.json()
                username = js.get("username", "")
                password = js.get("password", "")
            except:
                pass
        inputs = Login(
            username=username or "", password=SecretStr(password or ""))
        username = await helper.verify_user(inputs.username, inputs.password)
        if not (username and password):
            if helper.wants(request, MediaType.HTML):
                return Template("login.html", 
                                context={"failure": bool(form.get("check"))})
            raise NotAuthorizedException("invalid credentials")
        token = encode_jwt_token(user_id=username)
        logger.info(
            f"{request.method} /-/login {username} in {helper.now() - t0}")
        if helper.wants(request, MediaType.HTML):
            response: Any = Redirect("/")
        else:
            response = Response(content={
                "username": username, "success": True, HEADER_KEY: token})
        response.set_cookie(key=TOKEN_KEY, value=token)
        return response

    @route("/-/logout", http_method=["GET", "POST", "DELETE", "PUT"])
    async def logout(self, request: Request) -> Union[Response, Redirect]:
        if helper.wants(request):
            response: Any = Redirect("/")
        else:
            response = Response(content={"username": None, "success": True})
        response.delete_cookie(key=TOKEN_KEY)
        return response
