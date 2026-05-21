import pytest
from tests.test_ray import Environment, env
from kodosumi.core import ServeAPI, Launch, Tracer
from kodosumi.service.inputs.forms import InputText, Checkbox, InputFiles, Submit, Cancel, Model
from fastapi import Request
from ray import serve


async def runner1(inputs: dict, tracer: Tracer):
    return {"ok": True}



def factory1():

    app = ServeAPI()

    form_model = Model(
        InputText(label="Runner", name="runner"),
        Checkbox(label="Error", name="throw", value=False),
        InputFiles(label="Upload Files", name="files", multiple=True, 
                   directory=False),
        Submit("Submit"),
        Cancel("Cancel"),
    )

    @app.enter(
        "/runner",
        model=form_model,
        summary="Factory 1",
        deprecated=False,
        description="launches arbitrary runner",
    )
    async def form1(inputs: dict, request: Request) -> dict:
        runner = inputs.get("runner")
        throw = inputs.get("throw")
        if throw:
            raise Exception("test error")
        return Launch(request, runner1, inputs=inputs)

    @app.enter(
        "/deep/other",
        model=form_model,
        summary="Factory 1.a",
        deprecated=True,
        description="launches arbitrary runner (copy)",
    )
    async def form2(inputs: dict, request: Request) -> dict:
        runner = inputs.get("runner")
        throw = inputs.get("throw")
        if throw:
            raise Exception("test error")
        return Launch(request, runner1, inputs=inputs)

    return app


@serve.deployment
@serve.ingress(factory1())
class IngressDeployment: pass


fast_app = IngressDeployment.bind()


@pytest.mark.asyncio
async def test_async_upload(env, tmp_path):
    await env.start_app("tests.test_unwrap:factory1")
    form_data = {
        "runner": "tests.test_unwrap:runner1"
    }
    resp = await env.post("/-/localhost/8125/-/runner", json=form_data)
    assert resp.status_code == 200
    fid = resp.json()["result"]
    status = await env.wait_for(fid, "finished", "error")
    assert status == "finished"


# ------------------------------------------------------------------------------

VAULT = "/Users/raum/Project/jkx/viewer/output"

def factory2():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    import os

    app = ServeAPI()

    # Mount static files (css, js) at /static
    # app.mount("/static", StaticFiles(directory=os.path.join(VAULT, "static")), name="static")

    @app.get("/serve", summary="jKx Knowledge Graph", entry=True,
             description="Serve Journey Knowledge Graph Vault.")
    async def home(request: Request):
        return FileResponse(os.path.join(VAULT, "index.html"))

    @app.get("/serve/{path:path}")
    async def serve_file(path: str, request: Request):
        file_path = os.path.join(VAULT, path)
        # Check if path is a directory, serve index.html from it
        if os.path.isdir(file_path):
            index_path = os.path.join(file_path, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
        # Serve the file directly if it exists
        if os.path.exists(file_path):
            return FileResponse(file_path)
        # Try adding .html extension
        html_path = file_path + ".html"
        if os.path.exists(html_path):
            return FileResponse(html_path)
        return {"error": "Not found"}, 404

    return app


@serve.deployment
@serve.ingress(factory2())
class ObsidianDeployment: pass


fast_app2 = ObsidianDeployment.bind()

