import sys
from pathlib import Path
from typing import Annotated

import uvicorn
from crewai import Agent, Crew, Process, Task
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from ray import serve

from kodosumi.core import Launch, ServeAPI, Templates

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

class HymnRequest(BaseModel):
    topic: str


app = ServeAPI()

templates = Templates(
    directory=Path(__file__).parent.joinpath("templates"))

@app.get("/", summary="Hymn Creator",
            description="Creates a short hymn using openai and crewai.",
            version="1.0.0",
            author="m.rau@house-of-communication.com",
            tags=["CrewAI", "Test"],
            entry=True)
async def get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="hymn.html", context={})

@app.post("/", entry=True)
async def post(request: Request, 
                data: Annotated[HymnRequest, Form()]) -> Response:
    return Launch(request, "apps.example3:crew", data, reference=get)


@serve.deployment
@serve.ingress(app)
class Example1: pass


fast_app = Example1.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example3:app", host="0.0.0.0", port=8002, reload=True)
