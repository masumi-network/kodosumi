from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from ray.serve import deployment, ingress
from pathlib import Path
from kodosumi.serve import ServeAPI, Launch


app = ServeAPI()
templates = Jinja2Templates(
    directory=Path(__file__).parent.joinpath("templates"))


@deployment
@ingress(app)
class Posting:


    @app.get("/", 
             name="Job Posting", 
             description="Use CrewAI to automate the creation of job posting")
    async def get(self, request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="posting.html", context={})

    @app.post("/")
    async def post(self, request: Request) -> JSONResponse:
        form_data = await request.form()
        company_domain = form_data.get("company_domain")
        company_description = form_data.get("company_description")
        hiring_needs = form_data.get("hiring_needs")
        specific_benefits = form_data.get("specific_benefits")
        return Launch(request, "app2.crew:JobPostingCrew", {
            "company_domain": company_domain,
            "company_description": company_description,
            "hiring_needs": hiring_needs,
            "specific_benefits": specific_benefits,
        })


fast_app = Posting.bind()  # type: ignore
