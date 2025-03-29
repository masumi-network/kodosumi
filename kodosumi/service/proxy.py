from typing import Optional, Union

import litestar
from bs4 import BeautifulSoup
from httpx import AsyncClient
from litestar import MediaType, Request, route
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import Redirect, Response

from kodosumi import helper
from kodosumi.log import logger
from kodosumi.runner.const import KODOSUMI_LAUNCH

KODOSUMI_USER = "x-kodosumi_user"
KODOSUMI_BASE = "x-kodosumi_base"


def update_links(base_url, html_content) -> str:
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup.find_all(['a', 'link', 'script', 'img', 'form']):
        if tag.name == 'a' or tag.name == 'link':
            href = tag.get('href')
            if href and not href.startswith(('http://', 'https://')):
                if href.startswith(":"):
                    tag['href'] = href.lstrip(':')
                elif href.startswith("/"):
                    tag['href'] = base_url + href.lstrip('/')
        elif tag.name == 'form':
            action = tag.get('action')
            if action and not action.startswith(('http://', 'https://')):
                if action.startswith("/"):
                    tag['action'] = base_url + action.lstrip('/')
        else:
            src = tag.get('src')
            if src and not src.startswith(('http://', 'https://')):
                if src.startswith(":"):
                    tag['src'] = src.lstrip(':')
                elif src.startswith("/"):
                    tag['src'] = base_url + src.lstrip('/')
    return str(soup)


class ProxyControl(litestar.Controller):

    tags = ["Reverse Proxy"]
    include_in_schema = False

    @route("/{path:path}",
           http_method=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def forward(
            self,
            state: State,
            request: Request,
            path: Optional[str] = None) -> Union[Response, Redirect]:
        if path is None:
            path = "/-"
        path += "/"
        if "/-/" not in path:
            raise NotFoundException(path)
        base, relpath = path.split("/-/", 1)
        base += "/-/"
        relpath = relpath.rstrip("/")
        target = state["routing"].get(base)
        if not target:
            raise NotFoundException(path)
        timeout = state["settings"].PROXY_TIMEOUT
        async with AsyncClient(timeout=timeout) as client:
            meth = request.method.lower()
            request_headers = dict(request.headers)
            request_headers[KODOSUMI_USER] = request.user
            request_headers[KODOSUMI_BASE] = base
            host = request.headers.get("host", None)
            contact = target + "/"
            if relpath:
                contact += relpath
            response = await client.request(
                method=meth,
                url=contact,
                headers=request_headers,
                content=await request.body(),
                params=request.query_params,
                follow_redirects=True)
            response_headers = dict(response.headers)
            if host:
                response_headers["host"] = host
            response_headers.pop("content-length", None)
            if response.status_code == 200:
                fid1 = response.headers.get(KODOSUMI_LAUNCH, "")
                if fid1:
                    fid2 = response.json().get("fid", "")
                    if fid1 == fid2:
                        if helper.wants(request, MediaType.HTML):
                           return Redirect(f"/admin/exec/{fid1}")
                        if helper.wants(request, MediaType.TEXT):
                            return Redirect(f"/exec/state/{fid1}")
                        return Redirect(f"/exec/event/{fid1}")
            else:
                logger.error(
                    f"Proxy error: {response.status_code} {response.text}")
            response_content = response.content
            if response.headers.get("content-type", "").startswith("text/html"):
                response_content = update_links(
                    "/-" + base, response.content.decode("utf-8")).encode(
                        "utf-8")
        return Response(
                content=response_content,
                status_code=response.status_code,
                headers=response_headers)
