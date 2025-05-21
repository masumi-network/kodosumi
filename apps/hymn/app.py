import sys
from pathlib import Path

import fastapi
import uvicorn
from crewai import Agent, Crew, Process, Task
from ray import serve

import kodosumi.core as core
from kodosumi.core import ServeAPI
from kodosumi.core import forms as F

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


app = ServeAPI()


hymn_model = core.forms.Model(
    F.Markdown("""
    # Hymn Creator
                
    This agent creates a short hymn about a given topic of your choice using openai and crewai.
    """),
    F.Break(),
    F.InputText(
        label="Topic", name="topic", value="A Du Du Du and A Da Da Da."),
    F.Submit("Submit"),
    F.Cancel("Cancel")
)

@app.enter(
        path="/", 
        model=hymn_model,
        summary="Hymn Creator",
        description="This agent creates a short hymn about a given topic of your choice using openai and crewai.",
        version="1.0.1",
        author="m.rau@house-of-communication.com",
        tags=["Test", "CrewAi"]
    )
async def enter(request: fastapi.Request, inputs: dict):
    topic = inputs.get("topic", "").strip()
    if not topic:
        raise core.InputsError(topic="Please give me a topic.")
    return core.Launch(request, "hymn.app:crew", inputs=inputs)


@serve.deployment
@serve.ingress(app)
class HymnCreator: pass

fast_app = HymnCreator.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    import uvicorn
    uvicorn.run("hymn.app:app", host="0.0.0.0", port=8002, reload=True)
