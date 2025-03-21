import asyncio

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Union

from kodosumi import helper
from kodosumi.service.endpoint import (KODOSUMI_API, KODOSUMI_AUTHOR,
                               KODOSUMI_ORGANIZATION)
from kodosumi.serve import Launch, ServeAPI


class RuntimeRequest(BaseModel):
    runtime: int = 10  


async def run(inputs: Union[dict, RuntimeRequest]):
    if isinstance(inputs, dict):
        inputs = RuntimeRequest(**inputs)
    t0 = helper.now()
    i = 0
    while helper.now() < t0 + inputs.runtime:
        print(f"{i} - Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.", flush=True)
        await asyncio.sleep(0.1)
        i += 1
    return {"runtime": inputs.runtime}

def create_app():

    app = ServeAPI()

    @app.get("/", tags=["flow"])
    async def get() -> HTMLResponse:
        return HTMLResponse(content="""
            <html><body>
            <form action="/form" method="post">
                <input type="text" value="10" name="runtime">
                <input type="submit" value="Submit">
            </form>
            </body></html>
        """)

    @app.post("/form")
    async def post_form(request: Request) -> JSONResponse:
        form = await request.form()
        runtime = int(str(form.get("runtime", 10)))
        return Launch(request, "tests.apps.serve_direct:run", 
                    {"runtime": runtime})

    @app.post("/", summary="Runner", 
            description="Runs a specified time and creates some output",
            openapi_extra={KODOSUMI_API: True,
                            KODOSUMI_AUTHOR: "m.rau",
                            KODOSUMI_ORGANIZATION: "Plan.Net Journey"})
    async def post(request: Request, 
                runtime_request: RuntimeRequest) -> JSONResponse:
        return Launch(request, "tests.apps.serve_direct:run", runtime_request)

    @app.get("/end1", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end1() -> str:
        return "end1"

    @app.get("/end2", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end2() -> str:
        return "end2"

    @app.get("/end3", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end3() -> str:
        return "end3"

    @app.get("/end4", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end4() -> str:
        return "end4"

    @app.get("/end5", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end5() -> str:
        return "end5"

    @app.get("/end6", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end6() -> str:
        return "end6"

    @app.get("/end7", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end7() -> str:
        return "end7"

    @app.get("/end8", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end8() -> str:
        return "end8"

    @app.get("/end9", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end9() -> str:
        return "end9"

    @app.get("/end10", tags=["flow"], openapi_extra={KODOSUMI_API: True})
    async def get_end10() -> str:
        return "end10"

    return app

if __name__ == "__main__":
    import uvicorn
    import sys
    from pathlib import Path
    wd = str(Path(__file__).parent.parent.parent)
    sys.path.append(wd)
    uvicorn.run("tests.apps.serve_direct:create_app", 
                host="localhost", port=8000, reload=True, factory=True)
