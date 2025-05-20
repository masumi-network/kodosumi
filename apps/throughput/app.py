import asyncio
import time
from random import randint

import fastapi
from lorem_text import lorem
from ray import serve

import kodosumi.core as core
from kodosumi.core import forms as F


app = core.ServeAPI()


async def _runner(seconds: float, results: float, stdios: float, 
                 tracer: core.Tracer):
    
    async def result_callback():
        await tracer.markdown(lorem.words(randint(10, 50)))

    async def stdio_callback():
        print(f'{lorem.words(randint(3, 10))}')

    result_interval = seconds / results
    stdio_interval = seconds / stdios

    await tracer.markdown(f"""
    # Inputs
    - **seconds**: {seconds}
    - **results**: {results}, interval: {seconds / results:1.1f}
    - **stdios**: {stdios}, interval: {seconds / stdios:1.1f}
    """)

    timer = time.time() - 1
    next_result_time = result_interval
    next_stdio_time = stdio_interval

    while not (time.time() - timer) >= seconds:
        now = time.time()
        if now - timer >= next_result_time:
            await result_callback()
            next_result_time += result_interval
        
        if now - timer >= next_stdio_time:
            await stdio_callback()
            next_stdio_time += stdio_interval

        await asyncio.sleep(0.0001)

    await tracer.markdown(f"**Done**")

async def execute(inputs: dict, tracer: core.Tracer):
    seconds = inputs.get("seconds", 10)
    results = inputs.get("results", 2)
    stdios = inputs.get("stdios", 20)    
    await _runner(seconds, results, stdios, tracer=tracer)
    
    return core.response.Markdown(f"""
    # Final Result
    {lorem.paragraph()}
    """)


def main():
    ret = asyncio.run(
        execute(
            inputs=dict(
                seconds=10, 
                results=2, 
                stdios=20
            ), 
            tracer=core.TracerMock()
        )
    )
    print("final", ret)


def validate_positive_float(
        inputs: dict, 
        key: str, 
        default: float, 
        error: core.InputsError) -> float:
    value = inputs.get(key, "")
    if value == "":
        return default
    try:
        num_value = float(value)
        if num_value <= 0:
            raise ValueError()
        return num_value
    except ValueError:
        error.add(**{key: "Must be a number not smaller than zero"})
        return default


@app.enter(
    path="/", 
    model=core.forms.Model(
        F.Markdown("""
        # Test Runner
                
        This application flow runs for the specified number of seconds 
        and creates the specified number of results and `stdout` stream
        messages.
        """),
        F.Break(),
        F.InputText(label="Seconds to run", name="seconds", placeholder="10"),
        F.InputText(label="Results", name="results", placeholder="3"),
        F.InputText(label="Stdout", name="stdios", placeholder="30"),
        F.Submit("Submit"),
        F.Cancel("Cancel")
    ),
    summary="Throughput Test",
    description="This application flow runs for the specified number of seconds and creates the specified number of results and `stdout` stream messages.",
    version="1.0.0",
    author="m.rau@house-of-communication.com",
    tags=["Test"]
)
async def enter(request: fastapi.Request, inputs: dict):
    error = core.InputsError()
    seconds = validate_positive_float(inputs, "seconds", 10, error)
    results = validate_positive_float(inputs, "results", 2, error)
    stdios = validate_positive_float(inputs, "stdios", 20, error)
    if stdios / float(seconds) > 100:
        error.add(stdios="Please create less than 1000 events per second.")
    if results / float(seconds) > 100:
        error.add(results="Please create less than 1000 events per second.")
    if error.has_errors():
        raise error
    return core.Launch(
        request, 
        execute, 
        inputs={
            "seconds": seconds, 
            "results": results, 
            "stdios": stdios
        }
    )



@serve.deployment
@serve.ingress(app)
class ThroughputRunner: pass

fast_app = ThroughputRunner.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    import uvicorn
    uvicorn.run(
        # with reload == True we pass the application as a factory string
        "throughput.app:app", 
        reload=True,
        host="0.0.0.0", 
        port=8001
    )
