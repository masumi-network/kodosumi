from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from ray.serve import deployment, ingress

from kodosumi.serve import ServeAPI


app = ServeAPI()



@deployment
@ingress(app)
class HymnTest:

    @app.get("/", 
             name="A simple Hymn Generator", 
             description="This is a simple test crew.")
    async def get(self) -> HTMLResponse:
        return HTMLResponse(content="""
<html>
    <body>
        <h1>Hymn Creator</h1>
        <form method="POST"><input type="text" name="topic"/>
    </body>
</html>'
""")

    @app.post("/")
    async def post(self, request: Request) -> JSONResponse:
        form_data = await request.form()
        topic = form_data.get("topic")
        fid = await request.state.execute_flow("tests.app.crew:crew", {"topic": topic})
        return JSONResponse(content={}, headers={"kodosumi_launch": fid})


fast_app = HymnTest.bind()  # type: ignore
