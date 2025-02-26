from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from ray.serve import deployment, ingress

from kodosumi.serve import ServeAPI


app = ServeAPI()


@deployment
@ingress(app)
class Posting:

    @app.get("/", name="A Job Posting Crew")
    async def get(self) -> HTMLResponse:
        return HTMLResponse(content="""
<html>
    <body>
        <h1>Hymn Creator</h1>
        <form method="POST">
            <input type="text" name="company_domain" value="careers.wbd.com"/>
            <input type="text" name="company_description" value="Warner Bros. Discovery is a premier global media and entertainment company, offering audiences the world’s most differentiated and complete portfolio of content, brands and franchises across television, film, sports, news, streaming and gaming. We're home to the world’s best storytellers, creating world-class products for consumers"/>
            <input type="text" name="hiring_needs" value="Production Assistant, for a TV production set in Los Angeles in June 2025"/>
            <input type="text" name="specific_benefits" value="Weekly Pay, Employee Meals, healthcare"/>
            <input type="submit" value="Submit">
        </form>
    </body>
</html>'
""")

    @app.post("/")
    async def post(self, request: Request) -> JSONResponse:
        form_data = await request.form()
        company_domain = form_data.get("company_domain")
        company_description = form_data.get("company_description")
        hiring_needs = form_data.get("hiring_needs")
        specific_benefits = form_data.get("specific_benefits")
        fid = await request.state.execute_flow("tests.app2.crew:JobPostingCrew", {
            "company_domain": company_domain,
            "company_description": company_description,
            "hiring_needs": hiring_needs,
            "specific_benefits": specific_benefits,
        })
        return JSONResponse(content={}, headers={"kodosumi_launch": fid})


fast_app = Posting.bind()  # type: ignore
