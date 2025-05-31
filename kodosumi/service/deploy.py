from typing import List
import asyncio
import httpx
from pathlib import Path
import sys
from subprocess import Popen, PIPE, STDOUT
import litestar
from typing import AsyncGenerator, Optional, List, Union, Dict, Tuple
from litestar import get, post, put, delete, Request
from litestar.datastructures import State
from litestar.response import Response, Stream
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED, HTTP_204_NO_CONTENT, HTTP_404_NOT_FOUND
from litestar.exceptions import NotFoundException
import ray
from kodosumi.service.jwt import operator_guard
from kodosumi.ops import deploy, shutdown, status
import kodosumi.service.endpoint
from kodosumi.config import Settings
import time
import json as jsonlib


@ray.remote
class Deployer:

    def __init__(self, settings):
        self._active = False
        self.settings = settings

    def config_file(self) -> Path:
        return Path(self.settings.YAML_BASE)

    def config_dir(self) -> Path:
        return self.config_file().parent

    def create(self, name: str, body: str) -> str:
        file = self.config_dir() / f"{name}.yaml"
        with file.open("w") as f:
            f.write(body)
        return body

    def read(self, name: str) -> str:
        file = self.config_dir() / f"{name}.yaml"
        with file.open("r") as f:
            return f.read()

    def listing(self) -> List[str]:
        folder = self.config_dir()
        config_file = self.config_file()
        return [f.stem for f in folder.glob("*.yaml") 
                if f.name != config_file.name]

    def delete(self, name: str) -> bool:
        file = self.config_dir() / f"{name}.yaml"
        if file.exists():
            file.unlink()
            return True
        return False

    def deploy(self):    
        koco = Path(sys.executable).parent / "koco"
        proc = Popen([koco, "deploy", str(self.config_file())], 
                     stdout=PIPE, stderr=STDOUT)
        (stdout, _) = proc.communicate()
        return stdout.decode()
        # url = self.settings.RAY_DASHBOARD + "/api/serve/applications/"
        # while True:
        #     resp = httpx.get(url, headers={"Accept": "application/json"})
        #     apps = resp.json()["applications"]
        #     if not apps:
        #         break
        #     status = {k: v["status"] for k, v in apps.items()}
        #     total = len(status)
        #     running = sum([1 for s in status.values() if s.lower() == "running"])
        #     yield f"running: {running}/{total}"
        #     if running == total:
        #         break
        #     time.sleep(2)

    def status_dict(self):
        koco = Path(sys.executable).parent / "koco"
        proc = Popen([koco, "deploy", "--json"], stdout=PIPE, stderr=STDOUT)
        (stdout, _) = proc.communicate()
        js = stdout.decode()
        if js:
            return jsonlib.loads(stdout.decode())
        return {}

    def status(self):    
        koco = Path(sys.executable).parent / "koco"
        proc = Popen([koco, "deploy"], stdout=PIPE, stderr=STDOUT)
        (stdout, _) = proc.communicate()
        return stdout.decode()

    def shutdown(self):    
        koco = Path(sys.executable).parent / "koco"
        proc = Popen([koco, "deploy", "--shutdown"], stdout=PIPE, stderr=STDOUT)
        (stdout, _) = proc.communicate()
        return stdout.decode()


def identify_head_node_constraint():
    for node in ray.nodes():
        if node["Alive"]:
            resource_keys = node["Resources"].keys()
            head = [h for h in resource_keys if "_head_" in h]
            if head:
                node = [h for h in resource_keys if h.startswith("node:") and h != head[0]]
                return {node[0]: 1}
    return None

def _get_deployer(settings: Settings) -> Deployer:
    constraint = identify_head_node_constraint()
    return Deployer.options(  # type: ignore
        resources=constraint).remote(settings)

async def _wait_for(*tasks) -> str:
    unready = list(tasks)
    while True:
        ready, unready = ray.wait(unready, timeout=1)
        if ready:
            ret = ray.get(ready)
            return ret[0]
        await asyncio.sleep(1)


class DeployControl(litestar.Controller):

    tags = ["Deployment"]
    guards=[operator_guard]


    @post("/{name:str}", summary="Create deployment",
          description="Creates a new YAML configuration")
    async def create_deployment(self, name: str, state: State, request: Request) -> Response:
        content = await request.body()
        deployer = _get_deployer(state["settings"])
        out = await _wait_for(
            deployer.create.remote(name, content.decode()))
        return Response(content=str(out), media_type="text/plain")

    @get("/{name:str}", summary="Read a deployment",
         description="Reads the content of a YAML configuration")
    async def read_deployment(self, name: str, state: State) -> Response:
        deployer = _get_deployer(state["settings"])
        try:
            out = await _wait_for(deployer.read.remote(name))
        except Exception as e:
            raise NotFoundException(detail=f"Deployment {name} not found")
        return Response(content=out, media_type="application/x-yaml")
    
    @get("/", summary="List all deployments",
         description="Returns a list of all YAML configurations")
    async def list_deployments(self, state: State) -> dict:
        deployer = _get_deployer(state["settings"])
        listing = await _wait_for(deployer.listing.remote())
        url = state["settings"].RAY_DASHBOARD + "/api/serve/applications/"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
            js = resp.json()
            apps = js["applications"]
        if apps:
            status = {k: v["status"] for k, v in apps.items()}
        else:
            status = {}
        ret = {}
        for elm in listing:
            if elm in status:
                ret[elm] = status[elm].lower()
            else:
                ret[elm] = "to deploy"
        for elm in status:
            if elm not in ret:
                ret[elm] = "to stop"
        return ret
    
    @delete("/{name:str}", summary="Delete a deployment",
            description="Removes a YAML configuration")
    async def delete_deployment(self, name: str, state: State) -> None:
        deployer = _get_deployer(state["settings"])
        await _wait_for(deployer.delete.remote(name))

class ServeControl(litestar.Controller):
    tags = ["Deployment"]
    guards=[operator_guard]
    
    @post("/", summary="Re-deploy all",
         description="Redeploys all active deployment configurations")
    async def deploy(self, state: State) -> Response:
        deployer = _get_deployer(state["settings"])
        out = await _wait_for(deployer.deploy.remote())
        return Response(content=str(out), media_type="text/plain")

    @delete("/", summary="Shutdown all",
         description="Shutdown all active deployments")
    async def shutdown(self, state: State) -> None:
        deployer = _get_deployer(state["settings"])
        await _wait_for(deployer.shutdown.remote())

