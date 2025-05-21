import fastapi
from ray import serve
from kodosumi.core import ServeAPI
from kodosumi.core import InputsError
from kodosumi.core import Launch
from kodosumi.core import forms as F


app = ServeAPI()


my_model = F.Model(
    F.Markdown("""
    # My Service
    Specify the number of tasks to run. Each tasks will take between 1 and 3 
    seconds to complete. The level of concurrency is determined by the size of
    the Ray cluster.
    """),
    F.Break(),
    F.InputText(label="Number of Tasks", name="tasks", value="25"),
    F.Submit("Submit"),
    F.Cancel("Cancel")
)


@app.enter(
    path="/",
    model=my_model,
    summary="My Service",
    description="Not so agentic example service.",
    tags=["Test"],
    version="1.0.0",
    author="your.email@domain.com")
async def enter(request: fastapi.Request, inputs: dict):
    tasks = inputs.get("tasks", 0)
    error = InputsError()
    if not tasks:
        error.add(tasks="Please specify the number of concurrent tasks.")
    if isinstance(tasks, str):
        tasks = int(tasks)
    MAX = 500
    if tasks > MAX:
        error.add(tasks=f"Maximum number of concurrent tasks is {MAX}")
    if error.has_errors():
        raise error
    return Launch(
        request, 
        "my_service.calc:execute", 
        inputs={"tasks": tasks}
    )



@serve.deployment
@serve.ingress(app)
class SimpleApp: pass

fast_app = SimpleApp.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent.parent))
    import uvicorn
    # with reload == True we pass the application as a factory string
    uvicorn.run(
        "my_service.app:app", host="0.0.0.0", port=8005, reload=True)
