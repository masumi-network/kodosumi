from crewai import Agent, Task, Crew, Process
from crewai_tools import tool
from langchain_openai import ChatOpenAI
import datetime
import httpx


llm = ChatOpenAI()
    
# Agents
story_architect = Agent(
    name="Hymn Architect",
    role="Hymn Planner",
    goal="Create a topic outline for a short hymn.",
    backstory="An experienced hymn author with a knack for engaging plots.",
    llm=llm,
    max_iter=10,
    verbose=True
)

narrative_writer = Agent(
    name="Hymn Writer",
    role="Hymn Writer",
    goal="Write a short hymn based on the outline with no more than 150 words.",
    backstory="A creative hymn writer who brings stories to life with vivid descriptions.",
    llm=llm,
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
