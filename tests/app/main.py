from fastapi import FastAPI, APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from ray import serve
#from ray.serve import deployment, ingress, start
import sys
from typing import Union
from pathlib import Path
from kodosumi.serve import ServeAPI, Launch
import datetime
import asyncio
from fastapi.templating import Jinja2Templates
from kodosumi import helper


app = ServeAPI()
templates = Jinja2Templates(
    directory=Path(__file__).parent.joinpath("templates"))


async def runflow(inputs: dict):
    runtime = int(inputs.get("runtime", 5))
    t0 = helper.now()
    i = 0
    while helper.now() < t0 + datetime.timedelta(seconds=int(runtime)):
        print(f"{i} - Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.", flush=True)
        await asyncio.sleep(0.1)
        i += 1
    return {"runtime": runtime}


@serve.deployment
@serve.ingress(app)
class AppTest:

    @app.get("/", 
             name="Test App", 
             description="This flow runs for the specified time.",
             response_model=None)
    async def get(self, 
                  request: Request) -> Union[HTMLResponse]:
        return templates.TemplateResponse(
            request=request, name="main.html", context={})

    @app.post("/", response_model=None)
    async def post(self, 
                   request: Request) -> Union[HTMLResponse, JSONResponse]:
        form_data = await request.form()
        runtime = form_data.get("runtime", "10")
        return Launch(request, "app.main:runflow", {"runtime": runtime})
        return await self.get(request)


fast_app = AppTest.bind()  # type: ignore