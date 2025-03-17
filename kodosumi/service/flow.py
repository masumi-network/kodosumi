from collections import Counter
from typing import List, Optional

import litestar
from litestar import get, post
from litestar.datastructures import State

import kodosumi.service.endpoint
from kodosumi.dtypes import EndpointResponse, Pagination, RegisterFlow


class FlowControl(litestar.Controller):

    @post("/register")
    async def register_flow(
            self,
            state: State,
            data: RegisterFlow) -> List[EndpointResponse]:
        return await kodosumi.service.endpoint.register(state, data.url)
        
    @get("/")
    async def list_flows(self,
                         state: State, 
                         q: Optional[str] = None,
                         pp: int = 10, 
                         p: int = 0) -> Pagination[EndpointResponse]:
        data = kodosumi.service.endpoint.get_endpoints(state, q)
        start = p * pp
        end = start + pp
        total = len(data)
        return Pagination(items=data[start:end], total=total, p=p, pp=pp)
    
    @get("/tags")
    async def list_tags(self, state: State) -> dict[str, int]:
        tags = [
            tag for nest in [
                ep.tags for ep in kodosumi.service.endpoint.get_endpoints(state)
            ] for tag in nest
        ]
        return dict(Counter(tags))

    @post("/unregister", status_code=204)
    async def unregister_flow(self,
                              data: RegisterFlow,
                              state: State) -> None:
        kodosumi.service.endpoint.unregister(state, data.url)
