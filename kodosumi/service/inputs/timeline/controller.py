from typing import Literal, Optional
import datetime
import litestar
from litestar import Request, get
from litestar.datastructures import State
from litestar.response import Response, Template

from pathlib import Path
from kodosumi.service.inputs.timeline.tool import load_page, MODES


class TimelineController(litestar.Controller):

    tags = ["Admin Panel"]
    include_in_schema = True

    @get("/view")
    async def get_timeline(self,
                           state: State,
                           request: Request) -> Template:
        return Template("timeline/timeline.html", context={})

    @get("/")
    async def get(self,
                  state: State,
                  request: Request,
                  mode: Optional[MODES]=MODES.NEXT,
                  pp: int=10,
                  q: Optional[str]=None,
                  origin: Optional[str]=None,
                  offset: Optional[str]=None,
                  timestamp: Optional[float]=None) -> Response:
        exec_dir = Path(state["settings"].EXEC_DIR).joinpath(request.user)
        ret = load_page(exec_dir, mode=mode, pp=pp, query=q, origin=origin,
                        offset=offset, timestamp=timestamp)
        if mode == MODES.NEXT and not ret.get("items", {}).get("append"):
            ret["offset"] = None
            
        return Response(content=ret)