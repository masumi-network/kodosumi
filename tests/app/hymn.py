from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from ray.serve import deployment, ingress

from kodosumi.serve import Launch, ServeAPI


app = ServeAPI()

templates = Jinja2Templates(
    directory=Path(__file__).parent.joinpath("templates"))

@deployment
@ingress(app)
class HymnTest:

    @app.get("/", 
             name="Hymn Generator", 
             description="Creates a short hymn using openai and crewai.")

    async def get(self, request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="hymn.html", context={})

    @app.post("/", response_model=None)
    async def post(self, request: Request):
        form_data = await request.form()
        topic: str = str(form_data.get("topic", ""))
        if topic.strip():
            return Launch(request, "app.crew:crew", {"topic": topic})
        return await self.get(request)

fast_app = HymnTest.bind()  # type: ignore
