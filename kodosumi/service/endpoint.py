import asyncio
from hashlib import md5
from typing import Dict, List, Optional
from urllib.parse import urlparse

import ray
from litestar.datastructures import State
from litestar.exceptions import NotFoundException

from kodosumi.const import NAMESPACE
from kodosumi.const import KODOSUMI_API
from kodosumi.const import KODOSUMI_AUTHOR
from kodosumi.const import KODOSUMI_ORGANIZATION
from kodosumi.dtypes import EndpointResponse
from kodosumi.helper import HTTPXClient
from kodosumi.log import logger

API_FIELDS: tuple = (
    "summary", "description", "tags", "deprecated", KODOSUMI_AUTHOR,
    KODOSUMI_ORGANIZATION)


async def _get_openapi(url: str) -> dict:
    async with HTTPXClient() as client:
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
                details["base_url"] = base_url + "/" + ext
                details["deprecated"] = details.get("deprecated") or False
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


async def init(state: State) -> None:
    """creates or retrieves an existing register actor"""
    try:
        actor = Register.options(  # type: ignore
            namespace=NAMESPACE,
            name="register",
            enable_task_events=False,
            lifetime="detached").remote()
        logger.info(f"created register actor: {actor}")
    except ValueError:
        actor = ray.get_actor("register", namespace=NAMESPACE)
        logger.info(f"retrieved register actor: {actor}")
    state["register"] = actor
    await load(state["settings"].REGISTER_FLOW, state)


async def destroy(state: State) -> None:
    register = state.get("register")
    if register:
        ray.kill(register)
        logger.info(f"removed register actor: {register}")
        state["register"] = None


async def register(state: State, source: str) -> List[EndpointResponse]:
    register = state["register"]
    js = await _get_openapi(source)
    if "paths" not in js:
        if source.endswith("/-/routes"):
            # we have a /-/routes endpoint
            root = "/".join(source.split("/")[:-2])
            ray.get(register.reset.remote(source))
            for specs in js.keys():
                prefix = specs if specs != "/" else ""
                url2 = root + prefix + "/openapi.json"
                js2 = await _get_openapi(url2)
                ret = _extract(url2, js2)
                ray.get(register.add.remote(source, ret["register"]))
    else:
        ret = _extract(source, js)
        ray.get(register.put.remote(source, ret["register"]))
    it = ray.get(register.get.remote(source))
    logger.info(f'registered {len(it)} from {source}')
    return sorted(it, key=lambda ep: ep.summary or "None")


def find(state: State, query: Optional[str] = None) -> List[EndpointResponse]:
    def _query(item):
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
    it = items(state)
    scope = [item for nest in it for item in nest if _query(item)]
    scope = sorted(scope, key=lambda ep: (ep.summary or "", ep.url))
    return scope


def keys(state: State) -> List[str]:
    ret = ray.get(state["register"].get_keys.remote())
    return sorted(ret)


def items(state: State) -> List:
    ret = ray.get(state["register"].get_items.remote())
    return ret


def reset(state: State, source: Optional[str] = None) -> None:
    ray.get(state["register"].reset.remote(source))


def raw(state: State) -> Dict[str, List[EndpointResponse]]:
    ret = ray.get(state["register"].get_endpoints.remote())
    return ret


async def unregister(state: State, openapi_url: str) -> None:
    actor = state["register"]
    keys = ray.get(actor.get_keys.remote())
    if openapi_url in keys:
        logger.info(f"unregistering from {openapi_url}")
        ray.get(state["register"].remove.remote(openapi_url))
    else:
        raise NotFoundException(openapi_url)


async def load(scope: List[str], state: State) -> None:
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


@ray.remote
class Register:

    def __init__(self):
        self.endpoints = {}

    def reset(self, source: Optional[str] = None) -> None:
        if source is None:
            self.endpoints = {}
        else:
            if source in self.endpoints:
                del self.endpoints[source]

    def add(self, source: str, endpoint: EndpointResponse) -> None:
        self.endpoints.setdefault(source, []).extend(endpoint)

    def put(self, source: str, endpoints: List[EndpointResponse]) -> None:
        self.endpoints[source] = endpoints

    def get_endpoints(self) -> Dict[str, List[EndpointResponse]]:
        return self.endpoints

    def get(self, source: str) -> List[EndpointResponse]:
        return self.endpoints.get(source, [])

    def remove(self, source: str) -> None:
        del self.endpoints[source]

    def get_keys(self) -> List[str]:
        return list(self.endpoints.keys())

    def get_items(self) -> List[EndpointResponse]:
        return list(self.endpoints.values())
