from hashlib import md5
from typing import List, Union
from urllib.parse import urlparse

from httpx import AsyncClient
from litestar.datastructures import State

from kodosumi.dtypes import EndpointResponse
from kodosumi.log import logger


_fields: tuple = ("summary", "description", "tags", "deprecated",
                  "x-author", "x-organization")


async def _get_openapi(url: str) -> dict:
    async with AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def _extract(source, url, js, state: State) -> List[EndpointResponse]:
    register = []
    if url.endswith("/"):
        url = url[:-1]
    if "servers" in js:
        url += js['servers'][0]['url']
    state["endpoints"][source] = []
    for path, specs in js.get("paths", {}).items():
        meta = specs.get("get")
        if meta:
            if ((path == "/" and meta.get("x-kodosumi", True))
                    or (meta.get("x-kodosumi", False))):
                details = {"url": url + path}
                for key in _fields:
                    target = key[2:] if key.startswith("x-") else key
                    details[target] = meta.get(key, None)
                details["tags"] = sorted(details["tags"] or [])
                _id = urlparse(details["url"])
                details["path"] = f"/{_id.hostname}/{_id.port}{_id.path}"
                if details["path"].endswith("/"):
                    details["path"] = details["path"][:-1]
                details["path"] += "/-/"
                details["uid"] = md5(details["path"].encode()).hexdigest()
                details["source"] = source
                state["endpoints"][source].append(details)
                model = EndpointResponse.model_validate(details)
                register.append(model)
                logger.debug(
                    f"register {model.path} ({model.uid}) from {source}")
    return register


async def register(state: State, source: str) -> List[EndpointResponse]:
    js = await _get_openapi(source)
    if "paths" not in js:
        # we have a /-/routes endpoint
        parts = source.split("/")
        root = "/".join(parts[:-2])
        state["endpoints"][source] = []
        for specs in js.keys():
            url2 = f"{root}{specs}/openapi.json"
            js2 = await _get_openapi(url2)
            parsed_url = urlparse(source)
            url = f"{parsed_url.scheme}://{parsed_url.hostname}"
            if parsed_url.port:
                url += f":{parsed_url.port}"
            state["endpoints"][source] += await _extract(
                source, url, js2, state)
    else:
        # we have a /openapi.json endpoint
        parsed_url = urlparse(source)
        url = f"{parsed_url.scheme}://{parsed_url.hostname}"
        if parsed_url.port:
            url += f":{parsed_url.port}"
        state["endpoints"][source] = await _extract(source, url, js, state)
    logger.info(f'registered {len(state["endpoints"][source])} from {source}')
    return sorted(state["endpoints"][source], key=lambda ep: ep.path)


def get_endpoints(state: State) -> List[EndpointResponse]:
    return [
        EndpointResponse.model_validate(item) 
            for nest in state["endpoints"].values() 
                for item in nest
    ]


def find_endpoint(state: State, base: str) -> Union[EndpointResponse, None]:
    found = [ep for ep in get_endpoints(state) if ep.path == base]
    if not found:
        return None
    return found[0]


def unregister(state: State, url: str) -> None:
    if url in state["endpoints"]:
        logger.info(f"unregistering from {url}")
        del state["endpoints"][url]
