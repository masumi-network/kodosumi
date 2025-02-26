from fastapi import FastAPI, APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from ray.serve import deployment, ingress, run
import sys
from typing import Union
from pathlib import Path
from kodosumi.serve import ServeAPI
import datetime
import asyncio
from kodosumi import helper

app = ServeAPI()


async def run(inputs: dict):
    runtime = inputs.get("runtime", 5)
    t0 = helper.now()
    i = 0
    while helper.now() < t0 + datetime.timedelta(seconds=int(runtime)):
        print(f"{i} - Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.", flush=True)
        await asyncio.sleep(0.5)
        i += 1
    return {"runtime": runtime}

@deployment
@ingress(app)
class AppTest:

    @app.get("/", 
             name="Serve App Test Entry point", 
             description="This is a simple test flow (Entry).")
    async def get(self) -> HTMLResponse:
        return HTMLResponse(content="""
<html>
    <body>
        <h1>Serve App Test </h1>
        <form method="POST"><input type="text" name="runtime"/>
    </body>
</html>'
""")

    @app.post("/")
    async def post(self, request: Request) -> JSONResponse:
        form_data = await request.form()
        runtime = form_data.get("runtime")
        fid = await request.state.execute_flow("tests.app.main:run", {"runtime": runtime})
        return JSONResponse(content={}, headers={"kodosumi_launch": fid})


fast_app = AppTest.bind()  # type: ignore
