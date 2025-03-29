import sys
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ray import serve
from kodosumi.core import Launch, ServeAPI, Templates


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
                data: Annotated[HymnRequest, Form()]) -> JSONResponse:
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
    uvicorn.run("apps.example6:app", host="0.0.0.0", port=8004, reload=True)
