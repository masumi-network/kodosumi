import sys
from pathlib import Path
from typing import Union

import fastapi
import uvicorn
from ray import serve

import kodosumi.core as core
from kodosumi.core import ServeAPI
from kodosumi.core import forms as F


app = ServeAPI()


def run(inputs: dict, tracer: core.Tracer):
    import prime.calculator as calculator
    start = inputs.get("start", "").strip()
    end = inputs.get("end", "").strip()
    tasks = inputs.get("tasks", "").strip()
    distribution = calculator.get_prime_gaps_distribution_parallel(
        int(start), int(end), int(tasks), tracer)
    return distribution

async def run_async(inputs: dict, tracer: core.Tracer):
    import prime.calculator as calculator
    start = inputs.get("start", "").strip()
    end = inputs.get("end", "").strip()
    tasks = inputs.get("tasks", "").strip()
    distribution = calculator.get_prime_gaps_distribution_parallel(
        int(start), int(end), int(tasks), tracer)
    return distribution


prime_model = core.forms.Model(
    F.Markdown("""
    # Prime Gap Distribution
                
    This agent creates a prime gap distribution for a given range of numbers. Specify the start and end of the range and the number of Ray tasks to run in parallel.
    """),
    F.Break(),
    F.InputText(label="Start", name="start", value="0"),
    F.InputText(label="End", name="end", value="10000000"),
    F.InputText(label="Tasks", name="tasks", value="200"),
    F.Submit("Submit"),
    F.Cancel("Cancel")
)


@app.enter(
        path="/", 
        model=prime_model,
        summary="Prime Gap Distribution",
        description="This agent creates a prime gap distribution for a given range of numbers.",
        version="0.1.0",
        author="m.rau@house-of-communication.com",
        organization="House of Communication",
        tags=["Test"]
    )
async def enter(request: fastapi.Request, inputs: dict):
    return core.Launch(request, "prime.app:run", inputs=inputs)


@app.enter(
        path="/async", 
        model=prime_model,
        summary="Prime Gap Distribution (async)",
        description="This agent creates a prime gap distribution for a given range of numbers. This version implements the async pattern.",
        version="0.1.0",
        author="m.rau@house-of-communication.com",
        organization="House of Communication",
        tags=["Test"]
    )
async def enter_a(request: fastapi.Request, inputs: dict):
    return core.Launch(request, "prime.app:run_async", inputs=inputs)


@serve.deployment
@serve.ingress(app)
class PrimeDistribution: pass

fast_app = PrimeDistribution.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    import uvicorn
    uvicorn.run("prime.app:app", host="0.0.0.0", port=8004, reload=True)
