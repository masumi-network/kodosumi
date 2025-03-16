import asyncio

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from ray import serve

from kodosumi import helper
from kodosumi.service.endpoint import (KODOSUMI_API, KODOSUMI_AUTHOR,
                               KODOSUMI_ORGANIZATION)
from kodosumi.serve import Launch, ServeAPI


class RuntimeRequest(BaseModel):
    runtime: int = 10  


app = ServeAPI()


async def _run(runtime: int):
    t0 = helper.now()
    i = 0
    while helper.now() < t0 + runtime:
        print(f"{i} - Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.", flush=True)
        await asyncio.sleep(0.1)
        i += 1
    return {"runtime": runtime}


async def runflow(inputs: dict):
    return await _run(int(inputs.get("runtime", 5)))


async def runflow_with_schema(inputs: RuntimeRequest):
    return await _run(inputs.runtime)


@serve.deployment
@serve.ingress(app)
class AppTest:

    @app.get("/", openapi_extra={KODOSUMI_API: False})
    async def get(self) -> HTMLResponse:
        return HTMLResponse(content="""
            <html><body>
            <h1>Hidden Home Page</h1>
            </body></html>
        """)

    @app.get("/home", openapi_extra={KODOSUMI_API: True})
    async def home(self) -> HTMLResponse:
        return HTMLResponse(content="""
            <html><body>
            <li><a href="/">go hidden home</a></li>
            <li><a href="/page/1">go page 1</a></li>
            <li><a href="page/1">relative page 1</a></li>
            </body></html>
        """)

    @app.get("/page/1")
    async def page1(self) -> HTMLResponse:
        return HTMLResponse(content="""
            <html><body>
            <li><a href="../">go hidden home</a></li>
            <li><a href="./2">go page 2</a></li>
            </body></html>
        """)

    @app.get("/page/2")
    async def page2(self) -> HTMLResponse:
        return HTMLResponse(content="""
            <html><body>
            <li><a href="/home">go home</a></li>
            <li><a href="/page/1">go page 1</a></li>
            <li><a href="subpage/3">go page 3</a></li>
            </body></html>
        """)

    @app.get("/page/subpage/3")
    async def page2(self) -> HTMLResponse:
        return HTMLResponse(content="""
            <html><body>
            <li><a href="/home">go home</a></li>
            <li><a href="/page/1">go page 1</a></li>
            <li><a href="/page/subpage/3">go page 3</a></li>
            </body></html>
        """)

    @app.post("/page/post1")
    async def page_post1(self) -> str:
        return "OK"

    @app.post("/page/post2", openapi_extra={KODOSUMI_API: True})
    async def page_post2(self) -> str:
        return "OK"


fast_app = AppTest.bind()  # type: ignore

# serve run tests.apps.serve_href:fast_app --reload
# visit: http://localhost:8000/
