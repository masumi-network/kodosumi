import sys
from pathlib import Path
from typing import Annotated

import uvicorn
from crewai import Agent, Crew, Process, Task
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ray import serve

from kodosumi.core import Tracer, Launch, ServeAPI, Templates

class MarketingPostRequest(BaseModel):
    customer_domain: str
    project_description: str

app = ServeAPI()

templates = Templates(directory=Path(__file__).parent.joinpath("templates"))


@app.get("/", 
         summary="Marketing Posts",
         description="A crew of a Marketing Analyst, Strategist, and Content Creator, to create a marketing post for a company and project goal.",
         version="1.0.0",
         author="https://x.com/joaomdmoura",
         tags=["CrewAI", "Test"],
         entry=True)
async def get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="post.html", context={})

@app.post("/", entry=True)
async def post(request: Request, 
                data: Annotated[MarketingPostRequest, Form()]) -> Response:
    return Launch(request, "apps.example5.crew:MarketingPostsCrew", 
                  data, reference=get)


@serve.deployment
@serve.ingress(app)
class MarketingPostService: pass


fast_app = MarketingPostService.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example5.service:app",
                host="0.0.0.0", port=8004, reload=True)


