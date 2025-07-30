from multiprocessing import Process

import pytest
from fastapi import Request
from pydantic import BaseModel

from kodosumi.core import Launch, ServeAPI, Tracer
from kodosumi.service.inputs.forms import (Cancel, Checkbox, InputText, Model,
                                           Submit, InputFiles, Markdown)
from tests.test_execution import run_uvicorn


async def runner(inputs: dict, tracer: Tracer):
    await tracer.debug("this is a debug message")
    print("this is stdout")
    result = await tracer.lock("lock-1", data={"hello": "from runner"})
    return {"lock-result": result}
    # return {"Ergebnis": "ok"}

def app_factory():
    app = ServeAPI()
    form_model = Model(
        InputText(label="Name", name="name", placeholder="Enter your name"),
        Checkbox(label="Active", name="active", option="ACTIVE", value=False),
        InputFiles(label="Upload Files", name="upload", multiple=True, 
                   directory=True, required=True),
        Submit("GO"),
        Cancel("Cancel"),
    )

    class FormData(BaseModel):
        name: str
        active: bool = False

    @app.enter(
        "/",
        model=form_model,
        summary="File Upload Example",
        organization="Factory Organization",
        author="Factory Author",
        description="Factory Description",
    )
    async def post(inputs: dict, request: Request) -> dict:
        return Launch(request, "tests.test_inputs:runner", inputs=inputs)

    @app.lock("lock-1")
    async def lock_1():
        return Model(
            Markdown("# hello world"),
            Checkbox(label="Continue", name="continue",
                     option="CONTINUE", value=False),
            Submit("yes"),
            Cancel("no"),
        )

    # @app.lease("lock-1")
    # async def lease_1(inputs: dict):
    #     return {"phase": "lease-1", "inputs": inputs}

    return app


@pytest.fixture
def app_server():
    proc = Process(
        target=run_uvicorn,
        args=("tests.test_inputs:app_factory", 8125,))
    proc.start()
    yield f"http://localhost:8125"
    proc.kill()
    proc.terminate()
    proc.join()

