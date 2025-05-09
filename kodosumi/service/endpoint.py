from hashlib import md5
from typing import List, Optional, Union, List
from urllib.parse import urlparse
import asyncio
from httpx import AsyncClient
from litestar.datastructures import State
from litestar.exceptions import NotFoundException

from kodosumi.dtypes import EndpointResponse
from kodosumi.log import logger

KODOSUMI_API = "x-kodosumi"
KODOSUMI_AUTHOR = "x-author"
KODOSUMI_ORGANIZATION = "x-organization"

API_FIELDS: tuple = (
    "summary", "description", "tags", "deprecated", KODOSUMI_AUTHOR, 
    KODOSUMI_ORGANIZATION)


async def _get_openapi(url: str) -> dict:
    async with AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def _extract(openapi_url, js) -> dict:
    base_url = openapi_url
    if base_url.endswith("/"):
        base_url = base_url[:-1]
    base_url = "/".join(base_url.split("/")[:-1])
    base_elm = urlparse(base_url)
    root = f"/{base_elm.hostname}/{base_elm.port}{base_elm.path}/-/"
    register = []
    lookback = {}
    for path, specs in js.get("paths", {}).items():
        for meth, meta in specs.items():
            if meta.get(KODOSUMI_API, False):
                details = {"method": meth.upper()}
                for key in API_FIELDS:
                    target = key[2:] if key.startswith("x-") else key
                    details[target] = meta.get(key, None)
                details["tags"] = sorted(details["tags"] or [])
                ext = path.strip("/")
                details["url"] = "/-" + root + ext
                details["uid"] = md5(details["url"].encode()).hexdigest()
                details["source"] = openapi_url
                ep = EndpointResponse.model_validate(details)
                if meth == "get":
                    lookback[path] = ep
                elif path in lookback:
                    for field in ep.model_fields:
                        if getattr(ep, field) is None:
                            setattr(ep, field, getattr(lookback[path], field))
                register.append(ep)
                logger.debug(f"register {openapi_url}: {ep.url} ({ep.uid})")
    return {
        "root": root,
        "base_url": base_url,
        "register": register
    }

async def register(state: State, source: str) -> List[EndpointResponse]:
    js = await _get_openapi(source)
    if "paths" not in js:
        if source.endswith("/-/routes"):
            # we have a /-/routes endpoint
            root = "/".join(source.split("/")[:-2])
            state["endpoints"][source] = []
            for specs in js.keys():
                prefix = specs if specs != "/" else ""
                url2 = root + prefix + "/openapi.json"
                js2 = await _get_openapi(url2)
                ret = _extract(url2, js2)
                state["endpoints"][source] += ret["register"]
                state["routing"][ret["root"]] = ret["base_url"]
    else:
        ret = _extract(source, js)
        state["endpoints"][source] = ret["register"]
        state["routing"][ret["root"]] = ret["base_url"]
    logger.info(f'registered {len(state["endpoints"][source])} from {source}')
    return sorted(
        state["endpoints"][source], key=lambda ep: ep.summary or "None")


def get_endpoints(state: State, 
                  query: Optional[str]=None) -> List[EndpointResponse]:
    def find(item):
        if query is None:
            return True
        return query.lower() in "".join([
            str(i) for i in [
                item.summary, 
                item.description, 
                item.author, 
                item.organization, 
                "".join(item.tags)
            ] if i]).lower()  

    scope = [item for nest in state["endpoints"].values() 
             for item in nest if find(item)]
    scope = sorted(scope, key=lambda ep: (ep.summary or "", ep.url))
    return scope


def find_endpoint(state: State, base: str) -> Union[EndpointResponse, None]:
    found = [ep for ep in get_endpoints(state) if ep.url == base]
    if not found:
        return None
    return found[0]


def unregister(state: State, openapi_url: str) -> None:
    if openapi_url in state["endpoints"]:
        logger.info(f"unregistering from {openapi_url}")
        del state["endpoints"][openapi_url]
    else:
        raise NotFoundException(openapi_url)
    
async def reload(scope: List[str], state: State) -> None:
    for source in scope:
        trial = 3
        success = False
        while trial > 0:
            trial -= 1
            try:
                await register(state, source)
                success = True
                break
            except:
                await asyncio.sleep(1)
        if not success:
            logger.critical(f"failed to connect {source}")
