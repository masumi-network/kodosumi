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

    @app.get("/", tags=["flow", "test"])
    async def get(self) -> HTMLResponse:
        return HTMLResponse(content="""
            <html><body>
            <form action="/form" method="post">
                <input type="text" value="10" name="runtime">
                <input type="submit" value="Submit">
            </form>
            </body></html>
        """)

    @app.post("/form")
    async def post_form(self, request: Request) -> JSONResponse:
        form = await request.form()
        runtime = int(str(form.get("runtime", 10)))
        #return JSONResponse(content={"runtime": runtime})
        return Launch(
            request, "tests.apps.serve_app:runflow", {"runtime": runtime})

    @app.post("/", summary="Runner", 
              description="Runs a specified time and creates some output",
              openapi_extra={KODOSUMI_API: True,
                             KODOSUMI_AUTHOR: "m.rau",
                             KODOSUMI_ORGANIZATION: "Plan.Net Journey"})
    async def post(self, 
                   request: Request, 
                   runtime_request: RuntimeRequest) -> JSONResponse:
        return Launch(
            request, "tests.apps.serve_app:runflow_with_schema", 
            runtime_request)

    @app.post("/b/e/as_object",
              openapi_extra={KODOSUMI_API: True,
                             KODOSUMI_ORGANIZATION: "Plan.Net Journey"})
    async def post_object(self, 
                   request: Request, 
                   runtime_request: RuntimeRequest) -> JSONResponse:
        return Launch(request, runflow_with_schema, runtime_request)


fast_app = AppTest.bind()  # type: ignore

# serve run tests.apps.serve_app:fast_app --reload
# visit: http://localhost:8000/
