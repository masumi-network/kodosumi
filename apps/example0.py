import asyncio
from typing import Optional

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response
from ray import serve

from kodosumi import response
from kodosumi.core import Launch, ServeAPI, Tracer

app = ServeAPI()


async def execute(inputs: dict, tracer: Tracer):
    message = inputs.get("message")
    await tracer.markdown(f"* **Starting** ...")
    await tracer.debug(f"hello world")
    # do something else here ...
    await tracer.markdown(f"* **Done** ...")
    return response.Markdown(
        f"""# Hello World\nYou said {message}\n\n---\n\nI say _hello_!""")


@app.get("/", tags=["Test"], entry=True,
         summary="Hello World Example",
         description="Say hello world.",
         author="m.rau@house-of-communication.com",
         version="0.1.0")
@app.post("/", entry=True)
async def start(request: Request,
                message: Optional[str] = Form(None)) -> Response:
    if request.headers.get("content-type") == "application/json":
        try:
            body = await request.json()
            message = body.get("message")
        except Exception as e:
            return Response(
                status_code=400,
                content=f"Invalid JSON: {str(e)}",
                media_type="text/plain"
            )
    if message:
        return Launch(request, execute, inputs={"message": message})
    return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Hello World</h1>
                <p>This is a simple kodosumi example service.</p>
                <p>The service says Hello.</p>
                <form method="POST">
                    <textarea name="message" placeholder="Tell me something">What then?</textarea><br/>
                    <input type="submit"/>
                </form>
            </body>
        </html>
    """)
    

@serve.deployment
@serve.ingress(app)
class Example1: pass

fast_app = Example1.bind()  # type: ignore

if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example0:app", host="0.0.0.0", port=8001, reload=True)
