import sys
from pathlib import Path
import asyncio
import uvicorn
from fastapi import Request, Response
from ray import serve

from kodosumi.core import ServeAPI, Launch, Tracer
from kodosumi.service.inputs.forms import *
from kodosumi.service.inputs.errors import InputsError
app = ServeAPI()

model = Model(
    Markdown("# Test Enter 1"),
    Break(),
    InputText(label="Name", name="name", placeholder="Enter your name", required=True),
    Checkbox(option="Active", name="active", value=False),
    Submit("Submit"),
    Cancel("Cancel"),
)

async def runner(inputs: dict, tracer: Tracer) -> dict:
    await tracer.markdown("_starting up_")
    for i in range(100):
        await tracer.markdown(f"hello world {i}")
        await asyncio.sleep(0.1)
    return inputs

@app.enter("/", 
           model, 
           summary="Test Enter 1", 
           description="This is a highly simple test.",
           tags=["Test"])
async def enter(request: Request, inputs: dict) -> Response:
    error = InputsError()
    if not str(inputs["name"]).strip():
        error.add(name="Tell me your name")
    if not inputs["active"]:
        error.add(active="You have to be active")
    if error.has_errors():
        raise error
    return Launch(request, runner, inputs)

@serve.deployment
@serve.ingress(app)
class TestModel1: pass

fast_app1 = TestModel1.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example8:app", host="0.0.0.0", port=8009, reload=True)
