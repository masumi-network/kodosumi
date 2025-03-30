import sys
from pathlib import Path

import uvicorn
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse
from ray import serve
import apps.example7.main
from kodosumi.core import Launch, ServeAPI, Templates

app = ServeAPI()

templates = Templates(
    directory=Path(__file__).parent.joinpath("templates"))

@app.get("/", summary="Cluster Status",
            description="Say Hello to nodes in the cluster.",
            version="1.0.0",
            author="m.rau@house-of-communication.com",
            tags=["Test"],
            entry=True)
async def get(request: Request) -> HTMLResponse:
    resp = templates.TemplateResponse(
        request=request, name="cluster_status.html", context={})
    return resp

@app.post("/", entry=True)
async def post(request: Request, 
                n: str =Form()) -> Response:
    return Launch(request, 
                  "apps.example7.main:execute", 
                  inputs={"n": int(n)}, 
                  reference=get)


@serve.deployment
@serve.ingress(app)
class Example1: pass


fast_app = Example1.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example7.service:app", 
                host="0.0.0.0", 
                port=8005, 
                reload=True)
