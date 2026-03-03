import asyncio
import inspect
import json
from traceback import format_exc
from typing import Any, Callable, Optional, Tuple, Union

import ray.util.queue
import yaml
from bson.objectid import ObjectId
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import kodosumi
from kodosumi.config import Settings
from kodosumi.const import (EVENT_AGENT, EVENT_ERROR, EVENT_FINAL, EVENT_DEBUG,
                            EVENT_INPUTS, EVENT_META, EVENT_STATUS,
                            EVENT_PAYMENT, KODOSUMI_LAUNCH, NAMESPACE,
                            STATUS_END, STATUS_ERROR, STATUS_RUNNING,
                            STATUS_STARTING, TOKEN_KEY, EVENT_UPLOAD,
                            KODOSUMI_URL, HEADER_KEY, KODOSUMI_EXTRA,
                            STATUS_PAYMENT)
from kodosumi.helper import now, serialize
from kodosumi.runner.tracer import Tracer
from kodosumi.runner.payment import (
    MasumiClient, PaymentError, PaymentTimeoutError, create_result_hash
)
from kodosumi import dtypes

def parse_entry_point(entry_point: str) -> Callable:
    if ":" in entry_point:
        module_name, obj = entry_point.split(":", 1)
    else:
        *mod_list, obj = entry_point.split(".")
        module_name = ".".join(mod_list)
    module = __import__(module_name)
    components = module_name.split('.')
    for comp in components[1:]:
        module = getattr(module, comp)
    return getattr(module, obj)


@ray.remote
class Runner:
    def __init__(self,
                 fid: str,
                 username: str,
                 app_url: str,
                 entry_point: Union[Callable, str],
                 jwt: str,
                 panel_url: str,
                 inputs: Any=None,
                 method_info: Optional[dict]=None,
                 extra: Optional[dict]=None):
        self.fid = fid
        self.username = username
        self.app_url = app_url.rstrip("/")
        self.panel_url = panel_url.rstrip("/")
        self.entry_point = entry_point
        self.inputs = inputs
        self.method_info = method_info  # OpenAPI metadata from method lookup
        self.extra = extra  # User-provided extra data (e.g., identifier_from_purchaser)
        self.active = True
        self._payment: Optional[dict] = None
        self._payment_lock = asyncio.Lock()  # Prevents race condition in prepare()
        self._locks: dict = {}
        self.message_queue = ray.util.queue.Queue()
        self.tracer = Tracer(self.fid, self.message_queue, self.panel_url, jwt)
        self.tracer.init()

    async def get_username(self):
        return self.username

    async def get_queue(self):
        return self.message_queue

    def is_active(self):
        return self.active

    async def get_payment_config(self) -> Optional[dict]:
        """
        Get payment configuration from the extra dict passed by control.py.

        All payment-relevant data is passed via extra to avoid database
        access from Ray workers. Workers may run on different nodes without
        access to expose.db, causing "no such table: expose" errors.

        Required fields in extra (set by control.py _submit_job):
        - sumi_endpoint: indicates this is a Sumi protocol job
        - agentIdentifier: payment is configured if present
        - network: blockchain network (Preprod/Mainnet)
        - identifier_from_purchaser: customer job identifier
        - input_hash: hash of input data

        Returns:
            Dict with payment config if this is a paid flow, None otherwise.
        """
        if not self.extra:
            await self._put_async(EVENT_DEBUG, "get_payment_config: no extra")
            return None

        # Check if this is a Sumi protocol job
        sumi_endpoint = self.extra.get("sumi_endpoint")
        if not sumi_endpoint:
            await self._put_async(EVENT_DEBUG, "get_payment_config: no sumi_endpoint (not a Sumi job)")
            return None

        # Extract payment config from extra
        agent_identifier = self.extra.get("agentIdentifier")
        network = self.extra.get("network")
        identifier_from_purchaser = self.extra.get("identifier_from_purchaser")
        input_hash = self.extra.get("input_hash")

        # agentIdentifier indicates payment is configured for this endpoint
        if not agent_identifier:
            await self._put_async(EVENT_DEBUG, "get_payment_config: no agentIdentifier (payment not configured)")
            return None

        if not identifier_from_purchaser or not input_hash:
            await self._put_async(EVENT_DEBUG, f"get_payment_config: missing id/hash: {identifier_from_purchaser=}, {input_hash=}")
            return None

        if not network:
            await self._put_async(EVENT_DEBUG, "get_payment_config: no network in extra")
            return None

        # This is a paid flow - all config from extra, no DB access needed
        return {
            "agentIdentifier": agent_identifier,
            "network": network,
            "identifier_from_purchaser": identifier_from_purchaser,
            "input_hash": input_hash,
        }

    async def prepare(self) -> Optional[dict]:
        """
        Initialize payment if configured for this job.

        Idempotent: returns cached result on subsequent calls.
        Can be called externally (e.g., by sumi endpoint) to retrieve
        payment init data, or internally by start() as fallback.

        IMPORTANT: Uses _payment_lock to prevent race condition.
        Without the lock, concurrent calls from control.py (runner.prepare.remote())
        and from run() -> start() -> prepare() can both pass the idempotency check
        before either sets self._payment, causing duplicate payment initialization
        on the Masumi network. See: https://github.com/user/kodosumi/issues/XXX

        Returns:
            Dict with payment context if payment is required, None otherwise.
            Contains: pay_conf, blockchain_identifier, pay_data
        """
        async with self._payment_lock:
            # Double-check after acquiring lock (another call may have completed)
            if self._payment is not None:
                await self._put_async(EVENT_DEBUG, f"self._payment = {self._payment}")
                return self._payment

            pay_conf = await self.get_payment_config()
            await self._put_async(EVENT_DEBUG, f"pay_conf = {pay_conf}")
            if not pay_conf:
                return None

            settings = Settings()
            masumi_cfg = settings.get_masumi(pay_conf["network"])
            masumi = MasumiClient(masumi_cfg)
            pay_resp = await masumi.init_payment(
                agent_identifier=pay_conf["agentIdentifier"],
                network=pay_conf["network"],
                input_hash=pay_conf["input_hash"],
                identifier_from_purchaser=pay_conf["identifier_from_purchaser"],
            )
            blockchain_identifier = pay_resp.get("data", {}).get(
                "blockchainIdentifier")
            if not blockchain_identifier:
                raise PaymentError(
                    f"Payment init did not return blockchainIdentifier: {pay_resp}"
                )
            pay_data = pay_resp.get("data", {})

            await self._put_async(EVENT_PAYMENT, serialize({
                "step": "initialized",
                "agentIdentifier": pay_conf["agentIdentifier"],
                "network": pay_conf["network"],
                "inputHash": pay_conf["input_hash"],
                "blockchainIdentifier": blockchain_identifier,
                "pay_data": pay_data,
            }))

            self._payment = {
                "pay_conf": pay_conf,
                "blockchain_identifier": blockchain_identifier,
                "pay_data": pay_data,
            }
            return self._payment

    async def run(self):
        final_kind = STATUS_END
        try:
            await self.start()
        except Exception as exc:
            final_kind = STATUS_ERROR
            await self._put_async(EVENT_ERROR, format_exc())
        finally:
            await self._put_async(EVENT_STATUS, final_kind)
            await self.shutdown()

    async def _put_async(self, kind: str, payload: Any):
        await self.message_queue.put_async({
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        })  

    def _put(self, kind: str, payload: Any):
        self.message_queue.put({
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        })  

    async def start(self):
        await self._put_async(EVENT_STATUS, STATUS_STARTING)
        # Debug: Show raw input_data from Sumi start_job request
        if self.extra and self.extra.get("raw_input_data") is not None:
            await self._put_async(EVENT_DEBUG, f"start_job_input_data = {serialize(self.extra['raw_input_data'])}")
        await self._put_async(EVENT_INPUTS, serialize(self.inputs))
        if not isinstance(self.entry_point, str):
            ep = self.entry_point
            module = getattr(ep, "__module__", None)
            name = getattr(ep, "__name__", repr(ep))
            rep_entry_point = f"{module}.{name}"
        else:
            rep_entry_point = self.entry_point
        if isinstance(self.entry_point, str):
            obj = parse_entry_point(self.entry_point)
        else:
            obj = self.entry_point
        origin = {"kodosumi": kodosumi.__version__}
        if isinstance(self.method_info, dict):
            for field in ("tags", "summary", "description", "deprecated"):
                origin[field] = self.method_info.get(field, None)
            openapi_extra = self.method_info.get("openapi_extra", {})
            for field in ("author", "organization", "version"):
                origin[field] = openapi_extra.get(f"x-{field}", None)
        # Build meta event
        meta_data = {
            "fid": self.fid,
            "username": self.username,
            "app_url": self.app_url,
            "panel_url": self.panel_url,
            "entry_point": rep_entry_point,
            **origin
        }
        # Add user-provided extra data under 'extra' key (backwards-compatible)
        if self.extra:
            meta_data["extra"] = self.extra
        await self._put_async(EVENT_META, serialize(meta_data))
        # obj is a decorated crew class
        if hasattr(obj, "is_crew_class"):
            obj = obj().crew()
        # obj is a crew
        if hasattr(obj, "kickoff"):
            obj.step_callback = self.tracer.action_sync
            obj.task_callback = self.tracer.result_sync
            if isinstance(self.inputs, BaseModel):
                data = self.inputs.model_dump()
            else:
                data = self.inputs
            await self.summary(obj)
            await self._put_async(EVENT_STATUS, STATUS_RUNNING)
            result = await obj.kickoff_async(inputs=data)
        else:
            sig = inspect.signature(obj)
            bound_args = sig.bind_partial()
            if 'inputs' in sig.parameters:
                bound_args.arguments['inputs'] = self.inputs
            if 'tracer' in sig.parameters:
                bound_args.arguments['tracer'] = self.tracer
            try:
                fs = await self.tracer.fs()
                files = await fs.ls("in/")
            except FileNotFoundError:
                files = None
            # todo: ugly fix to suppress 401 for annonymous access
            except:
                files = None
            finally:
                await fs.close()
            if files:
                data = dtypes.Upload.model_validate({
                     "files": [dtypes.File.model_validate(f) for f in files]
                })
                await self._put_async(EVENT_UPLOAD, serialize(data))
            bound_args.apply_defaults()

            # from kodosumi.helper import debug
            # debug()

            # Payment initialization (idempotent — returns cached if
            # already called externally, e.g. by sumi endpoint)
            payment = await self.prepare()

            # Await payment confirmation if required
            if payment:
                await self._put_async(EVENT_STATUS, STATUS_PAYMENT)
                masumi_cfg = Settings().get_masumi(payment["pay_conf"]["network"])
                masumi = MasumiClient(masumi_cfg)
                await masumi.wait_for_funds_locked(
                    blockchain_identifier=payment["blockchain_identifier"],
                    network=payment["pay_conf"]["network"],
                    pay_by_time=payment["pay_data"].get("payByTime"),
                )
            await self._put_async(EVENT_STATUS, STATUS_RUNNING)

            # Execute the job
            if inspect.iscoroutinefunction(obj):
                result = await obj(*bound_args.args, **bound_args.kwargs)
            else:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, obj, *bound_args.args, **bound_args.kwargs)

            # Finalize payment if this was a paid flow
            if payment:
                await self._finalize_payment(
                    blockchain_identifier=payment["blockchain_identifier"],
                    network=payment["pay_conf"]["network"],
                    result=result,
                )

        await self._put_async(EVENT_FINAL, serialize(result))
        return result

    async def summary(self, flow):
        for agent in flow.agents:
            dump = {
                "role": agent.role,
                "goal": agent.goal,
                "backstory": agent.backstory,
                "tools": []
            }
            for tool in agent.tools:
                dump["tools"].append({
                    "name": tool.name,
                    "description": tool.description
                })
            await self._put_async(EVENT_AGENT, serialize({"agent": dump}))
        for task in flow.tasks:
            dump = {
                "name": task.name,
                "description": task.description,
                "expected_output": task.expected_output,
                "agent": task.agent.role,
                "tools": []
            }
            for tool in agent.tools:
                dump["tools"].append({
                    "name": tool.name,
                    "description": tool.description
                })
            await self._put_async(EVENT_AGENT, serialize({"task": dump}))

    async def _finalize_payment(
        self,
        blockchain_identifier: str,
        network: str,
        result: Any,
    ) -> None:
        """
        Submit the job result to Masumi to finalize payment.

        Args:
            blockchain_identifier: The blockchain identifier from init_payment
            network: "Preprod" or "Mainnet"
            result: The job result to hash and submit
        """
        from kodosumi.config import Settings
        masumi_cfg = Settings().get_masumi(network)
        masumi = MasumiClient(masumi_cfg)
        result_hash = create_result_hash(result)


        submit_response = await masumi.submit_result(
            blockchain_identifier=blockchain_identifier,
            network=network,
            result_hash=result_hash,
        )
        await self._put_async(EVENT_PAYMENT, serialize({
            "step": "final_result",
            # "blockchainIdentifier": blockchain_identifier,
            "resultHash": result_hash,
        }))

    async def shutdown(self):
        try:
            queue_actor = self.message_queue.actor
            while True:
                done, _ = ray.wait([
                    queue_actor.empty.remote()], timeout=0.01)
                if done:
                    ret = await asyncio.gather(*done)
                    if ret:
                        if ret[0] == True:
                            break
                await asyncio.sleep(0.1)
            self.tracer.shutdown()
            self.message_queue.shutdown()
        except: 
            pass
        self.active = False
        return "Runner shutdown complete."
    
    def get_locks(self):
        return self._locks
    
    async def lock(self, 
                   name: str, 
                   lid: str, 
                   expires: float,
                   data: Optional[dict]=None):
        self._locks[lid] = {
            "name": name,
            "data": data,
            "result": None,
            "app_url": self.app_url,
            "app_url": self.app_url,
            "expires": expires
        }
        while True:
            if self._locks.get(lid, {}).get("result", None) is not None:
                break
            if now() > expires:
                self._locks.pop(lid) 
                raise TimeoutError(f"Lock {lid} expired at{expires}")
            await asyncio.sleep(1)
        return self._locks.pop(lid)["result"]

    async def lease(self, lid: str, result: Any):
        if lid in self._locks:
            if self._locks[lid]["result"] is None:
                self._locks[lid]["result"] = result
                return True
        return False


def kill_runner(fid: str):
    runner = ray.get_actor(fid, namespace=NAMESPACE)
    ray.kill(runner)


def create_runner(username: str,
                  app_url: str,
                  entry_point: Union[str, Callable],
                  inputs: Union[BaseModel, dict],
                  method_info: Optional[dict] = None,
                  extra: Optional[dict] = None,
                  jwt: Optional[str] = None,
                  panel_url: Optional[str] = None,
                  fid: Optional[str]= None) -> Tuple[str, Runner]:
    if fid is None:
        fid = str(ObjectId())
    actor = Runner.options(  # type: ignore
        namespace=NAMESPACE,
        name=fid,
        enable_task_events=False,
        lifetime="detached").remote(
            fid=fid,
            username=username,
            app_url=app_url,
            entry_point=entry_point,
            inputs=inputs,
            method_info=method_info,
            extra=extra,
            jwt=jwt,
            panel_url=panel_url
    )
    return fid, actor

def Launch(request: Any,
           entry_point: Union[Callable, str],
           inputs: Any=None,
           reference: Optional[Callable] = None,
           summary: Optional[str] = None,
           description: Optional[str] = None,
           extra: Optional[dict] = None) -> Any:
    """
    Launch a new execution.

    Args:
        request: FastAPI request object
        entry_point: Function or module:function string to execute
        inputs: Input data for the entry point
        reference: Optional callable reference for method lookup
        summary: Optional summary override
        description: Optional description override
        extra: Optional user-provided extra data (e.g., identifier_from_purchaser).
               This data is stored in the meta event under the 'extra' key.

    Returns:
        JSONResponse with fid and KODOSUMI_LAUNCH header
    """
    if reference is None:
        if hasattr(request.app, "_code_lookup"):
            for sf in inspect.stack():
                reference = request.app._code_lookup.get(sf.frame.f_code)
                if reference is not None:
                    break
    if reference is None:
        method_info = {}
    else:
        # Copy to avoid modifying the original lookup dict
        method_info = dict(request.app._method_lookup.get(reference) or {})
    if summary is not None:
        method_info["summary"] = summary
    if description is not None:
        method_info["description"] = description
    # from kodosumi.helper import debug
    # debug()
    extra_header = request.headers.get(KODOSUMI_EXTRA)
    if extra_header:
        if not extra:
            extra = {}
        extra.update(json.loads(extra_header))
    fid, runner = create_runner(
        username=request.state.user,
        app_url=request.state.prefix,
        entry_point=entry_point,
        inputs=inputs,
        method_info=method_info,
        extra=extra,
        jwt=request.cookies.get(TOKEN_KEY) or request.headers.get(HEADER_KEY),
        panel_url=str(request.headers.get(KODOSUMI_URL))
    )
    runner.run.remote()  # type: ignore
    return JSONResponse(content={"fid": fid}, headers={KODOSUMI_LAUNCH: fid})
