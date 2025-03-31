import sys
from pathlib import Path

import uvicorn
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse
from ray import serve
from kodosumi.core import Launch, ServeAPI, Templates

app = ServeAPI()

templates = Templates(directory=Path(__file__).parent.joinpath("templates"))

@app.get("/", entry=True, summary="Cluster Status", version="1.0.0", 
         tags=["Test"], description="Say Hello to nodes in the cluster.",
         author="m.rau@house-of-communication.com")
async def get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="cluster_status.html", context={})

@app.post("/", entry=True)
async def post(request: Request, n: str =Form()) -> Response:
    return Launch(request, 
                  "apps.example7.main:execute", 
                  inputs={"n": int(n)}, 
                  reference=get)

@serve.deployment
@serve.ingress(app)
class Example1: pass

fast_app = Example1.bind()

if __name__ == "__main__":
    import sys
    from pathlib import Path
    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run(
        "apps.example7.service:app", host="0.0.0.0", port=8005, reload=True)
