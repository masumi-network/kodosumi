"""
Flow control endpoints — reads from expose.db instead of the legacy Register-Actor.
"""
from collections import Counter
from hashlib import md5
from typing import List, Optional

import yaml
import litestar
from litestar import get, put
from litestar.datastructures import State

from kodosumi.dtypes import EndpointResponse
from kodosumi.service.expose import db as expose_db
from kodosumi.service.jwt import operator_guard


def _flows_from_expose(row: dict) -> List[EndpointResponse]:
    """Extract EndpointResponse objects from an expose row's meta."""
    name = row.get("name", "")
    meta_yaml = row.get("meta")
    if not meta_yaml:
        return []

    try:
        meta_list = yaml.safe_load(meta_yaml)
    except yaml.YAMLError:
        return []

    if not isinstance(meta_list, list):
        return []

    flows = []
    for entry in meta_list:
        url = entry.get("url", "")
        if not url:
            continue

        # Only include alive/enabled flows
        if not entry.get("enabled", True):
            continue

        data_str = entry.get("data", "")
        try:
            data = yaml.safe_load(data_str) if data_str else {}
            if not isinstance(data, dict):
                data = {}
        except yaml.YAMLError:
            data = {}

        author_data = data.get("author") or {}
        author_name = author_data.get("name") if isinstance(author_data, dict) else None
        org = author_data.get("organization") if isinstance(author_data, dict) else None

        proxy_url = f"/-/{name}{url}"
        ep = EndpointResponse(
            uid=md5(proxy_url.encode()).hexdigest(),
            method="POST",
            url=proxy_url,
            source=name,
            summary=data.get("display") or name,
            description=data.get("description"),
            deprecated=False,
            author=author_name,
            organization=org,
            tags=data.get("tags") or [],
            base_url=url,
        )
        flows.append(ep)
    return flows


async def _get_all_flows(q: Optional[str] = None) -> List[EndpointResponse]:
    """Get all flows from expose.db, optionally filtered by query string."""
    await expose_db.init_database()
    rows = await expose_db.get_all_exposes()

    flows = []
    for row in rows:
        if not row.get("enabled"):
            continue
        flows.extend(_flows_from_expose(row))

    if q:
        q_lower = q.lower()
        flows = [f for f in flows if q_lower in " ".join([
            str(v) for v in [
                f.summary, f.description, f.author, f.organization,
                " ".join(f.tags)
            ] if v
        ]).lower()]

    return sorted(flows, key=lambda ep: (ep.summary or "", ep.url))


class FlowControl(litestar.Controller):

    @get("/",
         summary="Retrieve registered Flows",
         description="Paginated list of registered Flows from expose database.",
         tags=["Flow Control"], operation_id="11_list_flows")
    async def list_flows(
            self,
            state: State,
            q: Optional[str] = None,
            pp: int = 10,
            offset: Optional[str] = None) -> dict:
        data = await _get_all_flows(q)
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
         tags=["Flow Control"], operation_id="12_list_tags")
    async def list_tags(self, state: State) -> dict[str, int]:
        flows = await _get_all_flows()
        tags = [tag for ep in flows for tag in ep.tags]
        return dict(Counter(tags))

    @get("/register", summary="Retrieve Flow Register",
         description="List expose sources.",
         tags=["Flow Control"], operation_id="14_list_register")
    async def list_register(self, state: State) -> dict:
        await expose_db.init_database()
        rows = await expose_db.get_all_exposes()
        names = sorted([r["name"] for r in rows if r.get("enabled")])
        return {"routes": names}

    @put("/register", summary="Refresh registered Flows",
         description="Re-read flow metadata from expose database.",
         status_code=200, tags=["Flow Operations"],
         guards=[operator_guard], operation_id="15_update_flows")
    async def update_flows(self, state: State) -> dict:
        flows = await _get_all_flows()
        return {
            "summaries": {f.summary for f in flows},
            "urls": {f.url for f in flows},
            "sources": {f.source for f in flows},
            "total": len(flows),
        }
