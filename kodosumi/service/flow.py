from collections import Counter
from typing import List, Optional

import litestar
from litestar import get, post, put
from litestar.datastructures import State

import kodosumi.service.endpoint as endpoint
from kodosumi.dtypes import EndpointResponse, RegisterFlow
from kodosumi.service.jwt import operator_guard


class FlowControl(litestar.Controller):

    @post("/register", 
          summary="Register Flows",
          description="Register one or multiple flow.", 
          tags=["Flow Operations"], 
          guards=[operator_guard], operation_id="10_register_flow")
    async def register_flow(
            self,
            state: State,
            data: RegisterFlow) -> List[EndpointResponse]:
        results = []
        for url in data.url:
            results.extend(await endpoint.register(state, url))
        return results
        
    @get("/", 
         summary="Retrieve registered Flows",
         description="Paginated list of Flows which did register.", 
         tags=["Flow Control"], operation_id="11_list_flows")
    async def list_flows(
            self,
            state: State, 
            q: Optional[str] = None,
            pp: int = 10, 
            offset: Optional[str] = None) -> dict:
        data = endpoint.find(state, q)
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
    
    @get("/tags", 
         summary="Retrieve Tag List",
         description="Retrieve Tag List of registered Flows.", 
         tags = ["Flow Control"], operation_id="12_list_tags")
    async def list_tags(self, state: State) -> dict[str, int]:
        tags = [
            tag for nest in [ep.tags for ep in endpoint.find(state)] 
            for tag in nest
        ]
        return dict(Counter(tags))

    @post("/unregister", 
          status_code=200, 
          summary="Unregister Flows",
          description="Remove previoiusly registered flow sources.", 
          tags=["Flow Operations"], 
          guards=[operator_guard], operation_id="13_unregister_flow")
    async def unregister_flow(self,
                              data: RegisterFlow,
                              state: State) -> dict:
        for url in data.url:
            await endpoint.unregister(state, url)
        return {"deletes": data.url}

    @get("/register", summary="Retrieve Flow Register",
         description="Retrieve list of Flow sources.", tags=["Flow Control"], operation_id="14_list_register")
    async def list_register(self,
                         state: State) -> dict:
        keys = endpoint.keys(state)
        return {"routes": sorted(keys),
                "registers": state["settings"].REGISTER_FLOW}

    @put("/register", summary="Refresh registered Flows",
         description="Reconnect to the OpenAPI specification of all registered Flow sources.", 
         status_code=200, tags=["Flow Operations"], 
         guards=[operator_guard], operation_id="15_update_flows")
    async def update_flows(self, state: State) -> dict:
        urls = set()
        sums = set()
        dels = set()
        srcs = set()
        items = endpoint.raw(state).items()
        origin = {ep.url for _, endpoints in items for ep in endpoints}
        for register, endpoints in items:
            srcs.add(str(register))
            for ep in endpoints:
                urls.add(ep.url)
                sums.add(ep.summary)
        for url in origin:
            if url not in urls:
                dels.add(url)
        for src in srcs:
            endpoint.reset(state, src)
        await endpoint.load(list(srcs), state)
        return {
            "summaries": sums,
            "urls": urls,
            "deletes": dels,
            "sources": srcs,
            "connected": sorted(endpoint.keys(state))
        }