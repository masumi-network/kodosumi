import sys
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from ray import serve

from kodosumi.core import Launch, ServeAPI, Templates


class JobPostingRequest(BaseModel):
    company_domain: str
    company_description: str
    hiring_needs: str
    specific_benefits: str


app = ServeAPI()

templates = Templates(directory=Path(__file__).parent.joinpath("templates"))
# app.mount("/static", StaticFiles(
#     directory=Path(__file__).parent.joinpath("static")), name="static")


@app.get("/", 
         summary="Job Posting",
         description="A crew to create a job posting for a company.",
         version="1.0.0",
         author="https://x.com/joaomdmoura",
         tags=["CrewAI", "Test"],
         entry=True)
async def get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="job_posting.html", context={})

@app.post("/", entry=True)
async def post(request: Request, 
                data: Annotated[JobPostingRequest, Form()]) -> Response:
    return Launch(request, "apps.example4.crew:JobPostingCrew", 
                  data, reference=get)


@serve.deployment
@serve.ingress(app)
class JobPostingService: pass


fast_app = JobPostingService.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example4.service:app",
                host="0.0.0.0", port=8003, reload=True)


