from collections import Counter
from typing import List, Optional

import litestar
from litestar import get, post, put
from litestar.datastructures import State

import kodosumi.service.endpoint
from kodosumi.dtypes import EndpointResponse, Pagination, RegisterFlow

class FlowControl(litestar.Controller):

    @post("/register", tags=["Flows"])
    async def register_flow(
            self,
            state: State,
            data: RegisterFlow) -> List[EndpointResponse]:
        results = []
        for url in data.url:
            results.extend(await kodosumi.service.endpoint.register(state, url))
        return results
        
    @get("/", tags=["Flows"])
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
    
    @get("/tags", tags=["Flows"])
    async def list_tags(self, state: State) -> dict[str, int]:
        tags = [
            tag for nest in [
                ep.tags for ep in kodosumi.service.endpoint.get_endpoints(state)
            ] for tag in nest
        ]
        return dict(Counter(tags))

    @post("/unregister", status_code=200, tags=["Flows"])
    async def unregister_flow(self,
                              data: RegisterFlow,
                              state: State) -> dict:
        for url in data.url:
            kodosumi.service.endpoint.unregister(state, url)
        return {"deletes": data.url}

    @get("/register", tags=["Flows"])
    async def list_register(self,
                         state: State) -> dict:
        return {"routes": sorted(state["endpoints"].keys())}

    @put("/register", status_code=200, tags=["Flows"])
    async def update_flows(self,
                         state: State) -> dict:
        urls = set()
        sums = set()
        dels = set()
        srcs = set()
        for register, endpoints in state["endpoints"].items():
            srcs.add(str(register))
            for endpoint in endpoints:
                urls.add(endpoint.url)
                sums.add(endpoint.summary)
        for url in state["routing"]:
            if url not in urls:
                dels.add(url)
        for src in srcs:
            state["endpoints"][src] = []
        for url in dels:
            state["routing"].pop(url)
        await kodosumi.service.endpoint.reload(list(srcs), state)
        return {
            "summaries": sums,
            "urls": urls,
            "deletes": dels,
            "sources": srcs,
            "connected": sorted(state["endpoints"].keys())
        }