from collections import Counter
from typing import List
from dataclasses import dataclass
from typing import Generic, List, Optional, TypeVar

import litestar
from litestar import get, post
from litestar.datastructures import State

import kodosumi.endpoint
from kodosumi.dtypes import EndpointResponse, RegisterFlow, Pagination
from litestar.pagination import AbstractSyncClassicPaginator, ClassicPagination


class FlowControl(litestar.Controller):

    @post("/flow/register")
    async def register_flow(
            self,
            state: State,
            data: RegisterFlow) -> List[EndpointResponse]:
        return await kodosumi.endpoint.register(state, data.url)
        
    @get("/flow")
    async def list_flows(self,
                         state: State, 
                         pp: int = 10, 
                         p: int = 0) -> Pagination[EndpointResponse]:
        data = kodosumi.endpoint.get_endpoints(state)
        start = p * pp
        end = start + pp
        return Pagination(items=data[start:end], total=len(data), p=p, pp=pp)
    
    @get("/flow/tags")
    async def list_tags(self, state: State) -> dict[str, int]:
        tags = [
            tag for nest in [
                ep.tags for ep in kodosumi.endpoint.get_endpoints(state)
            ] for tag in nest
        ]
        return dict(Counter(tags))

    @post("/flow/unregister", status_code=204)
    async def unregister_flow(self,
                              data: RegisterFlow,
                              state: State) -> None:
        kodosumi.endpoint.unregister(state, data.url)
