import sys
from pathlib import Path
import kodosumi.dtypes as dtypes
import uvicorn
from pydantic import BaseModel
from crewai import Agent, Crew, Process, Task
from typing import Annotated
from fastapi import FastAPI, Request, Form, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from kodosumi.serve import ServeAPI, Launch, KODOSUMI_API
from kodosumi import tracer
from kodosumi.helper import now

# Agents
story_architect = Agent(
    name="Hymn Architect",
    role="Hymn Planner",
    goal="Create a topic outline for a short hymn.",
    backstory="An experienced hymn author with a knack for engaging plots.",
    max_iter=10,
    verbose=True
)

narrative_writer = Agent(
    name="Hymn Writer",
    role="Hymn Writer",
    goal="Write a short hymn based on the outline with no more than 150 words.",
    backstory="A creative hymn writer who brings stories to life with vivid descriptions.",
    max_iter=10,
    verbose=True
)

# Tasks
task_outline = Task(
    name="Hymn Outline Creation",
    agent=story_architect,
    description='Generate a structured plot outline for a short hymn about "{topic}".',
    expected_output="A detailed plot outline with key tension arc."
)

task_story = Task(
    name="Story Writing",
    agent=narrative_writer,
    description="Write the full hymn using the outline details and tension arc.",
    context=[task_outline],
    expected_output="A complete short hymn about {topic} with a beginning, middle, and end."
)

# Crew
crew = Crew(
    agents=[
        story_architect, 
        narrative_writer,
    ],
    tasks=[
        task_outline, 
        task_story,
    ],
    process=Process.sequential,
    verbose=True
)

crew.__brief__ = {
    "summary": "Hymn Crew",
    "description": "A crew of agents working together to create a short hymn.",
    "author": "m.rau@house-of-communication.com",
    "organization": "Plan.Net"
}

class HymnRequest(BaseModel):
    topic: str


import time


def counter(inputs: dict):
    """
    Runtime Counter

    This is a simple runner which runs for a specified time and creates some output.
    """
    t0 = now()
    runtime = inputs.get("runtime", 5)
    i = 0
    while now() < t0 + runtime:
        print(f"{i} - Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.", flush=True)
        if i % 100 == 0:
            tracer.text("Counter", f"Counter is at {i}")
            tracer.markdown(""""
                # Überschrift
                            
                * eins
                * zwei
            """)
            tracer.html('<img src="https://upload.wikimedia.org/wikipedia/commons/thumb/8/8f/Example_image.svg/600px-Example_image.svg.png"/>')
        i += 1
        time.sleep(0.01)
    return dtypes.Markdown(body=f"""
    # Final Output
                    
    * `RUNTIME:` {runtime}
    * `COUNTER:` {i}

    **thank you.**
    """)
    #return {"runtime": runtime, "final counter": i}


def create_app() -> FastAPI:

    app = ServeAPI()

    templates = Jinja2Templates(
        directory=Path(__file__).parent.joinpath("templates"))
    app.mount("/static", StaticFiles(
        directory=Path(__file__).parent.joinpath("static")), name="static")

    @app.get("/", summary="Hymn Creator",
             description="Creates a short hymn using openai and crewai.")
    async def get(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="hymn.html", context={})

    @app.post("/", summary="Serve Real Apps (Intro)",
             description="Creates a short hymn using openai and crewai.")
    async def post(request: Request, 
                   data: Annotated[HymnRequest, Form()]) -> Response:
        return Launch(request, crew, data)

    @app.get("/counter", 
             summary="Runtime Counter", 
             description="A simple Agentic Service which is not really so agentic...", openapi_extra={KODOSUMI_API: True})
    async def get_counter(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="counter.html", context={})

    @app.post("/counter")
    async def post_counter(request: Request) -> Response:
        form = await request.form()
        runtime = int(str(form.get("runtime", 10)))
        return Launch(
            request, counter, {"runtime": runtime})

    return app


if __name__ == "__main__":
    wd = str(Path(__file__).parent.parent.parent)
    sys.path.append(wd)
    uvicorn.run("tests.apps.serve_real:create_app", 
                host="localhost", port=8002, reload=True, factory=True)
