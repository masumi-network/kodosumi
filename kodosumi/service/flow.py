from collections import Counter
from typing import List

import litestar
from litestar import get, post
from litestar.datastructures import State

import kodosumi.endpoint
from kodosumi.dtypes import EndpointResponse, RegisterFlow


class FlowControl(litestar.Controller):

    @post("/flow/register")
    async def register_flow(
            self,
            state: State,
            data: RegisterFlow) -> List[EndpointResponse]:
        return await kodosumi.endpoint.register(state, data.url)
        
    @get("/flow", response_model=List[EndpointResponse])
    async def list_flows(self, state: State) -> List[EndpointResponse]:
        return sorted(kodosumi.endpoint.get_endpoints(state), 
                      key=lambda ep: ep.path)
    
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
