from multiprocessing import Process

import pytest
from fastapi import Request
from pydantic import BaseModel

from kodosumi.core import Launch, ServeAPI, Tracer
from kodosumi.service.inputs.forms import (Cancel, Checkbox, InputText, Model,
                                           Submit, InputFiles, Markdown)
from tests.test_execution import run_uvicorn


async def runner(inputs: dict, tracer: Tracer):
    # result = await tracer.lock("lock-1", data={"hello": "from runner"})
    # return {"lock-result": result}
    # from kodosumi.helper import debug
    # debug()
    return {"Ergebnis": "ok"}

def app_factory():
    app = ServeAPI()

    @app.enter(
        "/",
        model=Model(
            Markdown("""# Upload Files"""),
            Markdown("""Upload one or multiple files and launch the job"""),
            InputText(label="Name", name="name", placeholder="Enter a name"),
            InputFiles(label="Upload Files", name="files", multiple=True, 
                    directory=False, required=True),
            Submit("GO"),
            Cancel("Cancel"),
        ),
        summary="File Upload Example",
        organization="Factory Organization",
        author="Factory Author",
        description="Factory Description",
    )
    async def post(inputs: dict, request: Request) -> dict:
        return Launch(request, "tests.test_inputs:runner", inputs=inputs)

    @app.enter(
        "/simple",
        model=Model(
            Markdown("""# Simple Example"""),
            InputText(label="Name", name="name", placeholder="Enter a name"),
            InputFiles(label="Upload Files", name="files", multiple=True, 
                    directory=False, required=False),
            Submit("GO"),
            Cancel("Cancel"),
        ),
        summary="Simple Example",
        organization="Factory Organization",
        author="Factory Author",
        description="Factory Description",
    )
    async def simple(inputs: dict, request: Request) -> dict:
        return Launch(request, "tests.test_inputs:runner", inputs=inputs)

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

