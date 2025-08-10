from typing import Literal, Optional
import datetime
import litestar
from litestar import Request, get
from litestar.datastructures import State
from litestar.response import Response, Template

from pathlib import Path
from kodosumi.service.inputs.timeline.tool import (load_page, MODES, 
                                                   get_paginated_results,
                                                   get_changes)


class TimelineController(litestar.Controller):

    tags = ["Execution Timeline"]

    @get("/", summary="Get Timeline",
         description="Retrieve the timeline of the user's executions.", operation_id="60_get_timeline", include_in_schema=False)
    async def get_timeline(self,
                  state: State,
                  request: Request,
                  mode: Optional[MODES] = MODES.NEXT,
                  pp: int = 10,
                  q: Optional[str] = None,
                  origin: Optional[str] = None,
                  offset: Optional[str] = None,
                  timestamp: Optional[float] = None) -> Response:
        exec_dir = Path(state["settings"].EXEC_DIR).joinpath(request.user)
        ret = load_page(exec_dir, mode=mode, pp=pp, query=q, origin=origin,
                        offset=offset, timestamp=timestamp)
        if mode == MODES.NEXT and not ret.get("items", {}).get("append"):
            ret["offset"] = None
        return Response(content=ret)

    @get("/list", summary="Get Paginated Timeline",
         description="Retrieve paginated timeline results sorted from fid offset.", operation_id="61_get_list")
    async def get_list(self,
                       state: State,
                       request: Request,
                       pp: int = 10,
                       q: Optional[str] = None,
                       offset: Optional[str] = None) -> Response:
        exec_dir = Path(state["settings"].EXEC_DIR).joinpath(request.user)
        ret = get_paginated_results(exec_dir, offset=offset, pp=pp, q=q)
        return Response(content=ret)

    @get("/changes", summary="Get Timeline Changes",
         description="Retrieve changes to timeline items since a given timestamp.", operation_id="62_get_changes")
    async def get_changes(self,
                          state: State,
                          request: Request,
                          since: Optional[float] = None,
                          q: Optional[str] = None) -> Response:
        exec_dir = Path(state["settings"].EXEC_DIR).joinpath(request.user)
        ret = get_changes(exec_dir, since_timestamp=since, q=q)
        return Response(content=ret)
