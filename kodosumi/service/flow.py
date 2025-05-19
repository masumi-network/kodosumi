from collections import Counter
from typing import List, Optional

import litestar
from litestar import get, post, put
from litestar.datastructures import State

import kodosumi.service.endpoint
from kodosumi.dtypes import EndpointResponse, RegisterFlow
from kodosumi.service.jwt import operator_guard


class FlowControl(litestar.Controller):

    @post("/register", summary="Register Flows",
         description="Register a Flow.", tags=["Flow Operations"], guards=[operator_guard])
    async def register_flow(
            self,
            state: State,
            data: RegisterFlow) -> List[EndpointResponse]:
        results = []
        for url in data.url:
            results.extend(await kodosumi.service.endpoint.register(state, url))
        return results
        
    @get("/", summary="Retrieve registered Flows",
         description="Paginated list of Flows which did register.", 
         tags=["Flow Control"])
    async def list_flows(
            self,
            state: State, 
            q: Optional[str] = None,
            pp: int = 10, 
            offset: Optional[str] = None) -> dict:
        data = kodosumi.service.endpoint.get_endpoints(state, q)
        total = len(data)
        start_idx = 0
        if offset:
            for i, item in enumerate(data):
                if item.uid == offset:
                    start_idx = i + 1
                    break
        end_idx = min(start_idx + pp, total)
        results = data[start_idx:end_idx]
        return {
            "items": results,
            "offset": results[-1].uid if results and end_idx < total else None
        }
    
    @get("/tags", summary="Retrieve Tag List",
         description="Retrieve Tag List of registered Flows.", 
         tags = ["Flow Control"])
    async def list_tags(self, state: State) -> dict[str, int]:
        tags = [
            tag for nest in [
                ep.tags for ep in kodosumi.service.endpoint.get_endpoints(state)
            ] for tag in nest
        ]
        return dict(Counter(tags))

    @post("/unregister", status_code=200, summary="Unregister Flows",
         description="Remove a previoiusly registered Flow source.", 
         tags=["Flow Operations"], guards=[operator_guard])
    async def unregister_flow(self,
                              data: RegisterFlow,
                              state: State) -> dict:
        for url in data.url:
            kodosumi.service.endpoint.unregister(state, url)
        return {"deletes": data.url}

    @get("/register", summary="Retrieve Flow Register",
         description="Retrieve list of Flow sources.", tags=["Flow Control"])
    async def list_register(self,
                         state: State) -> dict:
        return {"routes": sorted(state["endpoints"].keys()),
                "registers": state["settings"].REGISTER_FLOW}

    @put("/register", summary="Refresh registered Flows",
         description="Retrieve the OpenAPI specification of all registered Flow sources.", 
         status_code=200, tags=["Flow Operations"], 
         guards=[operator_guard])
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