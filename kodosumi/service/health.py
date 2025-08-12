import litestar
from litestar import Response, get

from kodosumi import helper


class HealthControl(litestar.Controller):

    tags = ["Health Check"]

    @get("/", summary="Provide kodosumi Health Check",
         description="Info of Ray, Serve, Spooler, Services and Version.", status_code=200, 
         operation_id="01_health_get")
    async def health_status(self) -> Response:
        status = helper.get_health_status()
        return Response(content=status)
