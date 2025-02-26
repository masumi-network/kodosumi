import litestar
from litestar import Request, get, post, route
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

    @get("/", opt={"no_auth": True})
    async def home(self) -> Redirect:
        return Redirect("/-/helo")

    @get("/favicon.ico", opt={"no_auth": True})
    async def favicon(self) -> Response:
        return Response(content=b"")

    async def _login(self, method, data: Login):
        t0 = helper.now()
        username = await helper.verify_user(data.username, data.password)
        if not username:
            raise NotAuthorizedException("invalid credentials")
        token = encode_jwt_token(user_id=username)
        response = Response(content={
            "username": username, "success": True, HEADER_KEY: token})
        response.set_cookie(key=TOKEN_KEY, value=token)
        logger.info(f"{method} /-/login {username} in {helper.now() - t0}")
        return response

    @get("/-/login", opt={"no_auth": True})
    async def login(self, username: str, password: str) -> Response:
        data = Login(username=username, password=SecretStr(password))
        return await self._login("GET", data)

    @get("/-/help")
    async def help(self) -> Response:
        return Template("help.html", context={})


    @post("/-/login", opt={"no_auth": True})
    async def post_login(self, data: Login) -> Response:
        return await self._login("POST", data)

    @route("/-/logout", http_method=["GET", "POST", "DELETE", "PUT"])
    async def logout(self) -> Response:
        response = Response(content={"username": None, "success": True})
        response.delete_cookie(key=TOKEN_KEY)
        return response
