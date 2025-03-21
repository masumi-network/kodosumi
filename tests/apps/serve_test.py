from pathlib import Path
from typing import Annotated
import time

from crewai import Agent, Crew, Process, Task
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from ray import serve

import kodosumi.dtypes as dtypes
from kodosumi import helper, tracer
from kodosumi.helper import now
from kodosumi.serve import KODOSUMI_API, Launch, ServeAPI

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
        if i % 5 == 0:
            tracer.text("Counter", f"at {i}")
        i += 1
        time.sleep(0.1)
    return dtypes.Markdown(body=f"""
    ### Final Output
    * RUNTIME: {runtime}
    * COUNTER: {i}
    """)


app = ServeAPI()
templates = Jinja2Templates(
    directory=Path(__file__).parent.joinpath("templates"))
app.mount("/static", StaticFiles(
    directory=Path(__file__).parent.joinpath("static")), name="static")


@serve.deployment
@serve.ingress(app)
class AppTest:

    @app.get("/", summary="Hymn Creator",
             description="Creates a short hymn using openai and crewai.")
    async def get(self, request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="hymn.html", context={})

    @app.post("/", summary="Serve Real Apps (Intro)",
             description="Creates a short hymn using openai and crewai.")
    async def post(self, request: Request, 
                   data: Annotated[HymnRequest, Form()]) -> Response:
        return Launch(request, crew, data)

    @app.get("/counter", 
             summary="Runtime Counter", 
             description="A simple Agentic Service which is not really so agentic...", openapi_extra={KODOSUMI_API: True})
    async def get_counter(self, request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="counter.html", context={})

    @app.post("/counter")
    async def post_counter(self, request: Request) -> Response:
        form = await request.form()
        runtime = int(str(form.get("runtime", 10)))
        return Launch(
            request, counter, {"runtime": runtime})

fast_app = AppTest.bind()  # type: ignore
