from fastapi.responses import HTMLResponse
from ray import serve
from kodosumi.serve import ServeAPI
from pathlib import Path


app = ServeAPI()


@app.get("/", tags=["test"], entry=True)
async def get() -> HTMLResponse:
    return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Hello World</h1>
                <h2>{__file__}</h2>
                <p>This is a simple kodosumi example app.</p>
            </body>
        </html>
    """)
    

@serve.deployment
@serve.ingress(app)
class AppTest1: pass

fast_app = AppTest1.bind()  # type: ignore

if __name__ == "__main__":
    import uvicorn
    import sys
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example1:app", host="0.0.0.0", port=8002, reload=True)
