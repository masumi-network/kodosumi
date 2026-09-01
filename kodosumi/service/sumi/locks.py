"""
Sumi Protocol Controller for locks - MIP-003 endpoints of a lock.

A lock bundles several flows behind one endpoint. This controller serves
its input schema and forwards jobs and inputs to the locked flows.
"""

import json
import time

import ray

from litestar import Controller, get, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException

from kodosumi.helper import HTTPXClient
from kodosumi.service.jwt import sumi_job_network_guard
from kodosumi.service.proxy import LockNotFound, find_lock
from kodosumi.service.sumi.hash import create_input_hash
from kodosumi.service.sumi.models import (
    LockSchemaResponse, ProvideInputRequest, ProvideInputResponse)
from kodosumi.service.sumi.schema import (
    convert_model_to_schema, convert_mip003_indices_to_values,
    create_empty_schema)


class SumiLockControl(Controller):
    """
    Sumi Protocol Lock Controller.

    Provides MIP-003 compliant lock/provide_input endpoints.
    """

    path = "/sumi/lock"
    tags = ["Sumi Protocol"]

    @get(
        "/{fid:str}/{lid:str}",
        summary="Get lock schema",
        description="Get MIP-003 compliant input schema for a pending lock.",
        operation_id="sumi_get_lock",
        opt={"no_auth": True},
        guards=[sumi_job_network_guard],
    )
    async def get_lock_schema(
        self,
        state: State,
        fid: str,
        lid: str,
    ) -> LockSchemaResponse:
        """
        Get input schema for a pending lock.

        Args:
            fid: Job ID (execution ID)
            lid: Lock ID
        """
        try:
            lock, actor = find_lock(fid, lid)
        except LockNotFound as e:
            raise NotFoundException(detail=e.message)

        # Check if lock is already released
        if lock.get("result") is not None:
            return LockSchemaResponse(
                job_id=fid,
                status_id=lid,
                status="released",
                input_schema=create_empty_schema(),
                expires_at=lock.get("expires"),
                prompt=None,
            )

        # Get schema from lock endpoint
        target = f"{lock['app_url']}/_lock_/{fid}/{lid}"

        try:
            async with HTTPXClient() as client:
                resp = await client.get(target, timeout=10.0)

            if resp.status_code != 200:
                return LockSchemaResponse(
                    job_id=fid,
                    status_id=lid,
                    status="pending",
                    input_schema=create_empty_schema(),
                    expires_at=lock.get("expires"),
                    prompt=None,
                )

            elements = resp.json()

            # Convert to MIP-003 schema
            input_schema = convert_model_to_schema(elements)

            return LockSchemaResponse(
                job_id=fid,
                status_id=lid,
                status="pending",
                input_schema=input_schema,
                expires_at=lock.get("expires"),
                prompt=lock.get("name"),  # Lock name can serve as prompt
            )

        except Exception:
            return LockSchemaResponse(
                job_id=fid,
                status_id=lid,
                status="pending",
                input_schema=create_empty_schema(),
                expires_at=lock.get("expires"),
                prompt=None,
            )

    @post(
        "/{fid:str}/{lid:str}",
        summary="Provide input to lock",
        description="MIP-003 compliant provide_input to release a lock.",
        operation_id="sumi_provide_input",
        opt={"no_auth": True},
        guards=[sumi_job_network_guard],
    )
    async def provide_input(
        self,
        state: State,
        fid: str,
        lid: str,
        data: ProvideInputRequest,
    ) -> ProvideInputResponse:
        """
        Provide input to release a pending lock.

        Args:
            fid: Job ID (execution ID)
            lid: Lock ID
            data: ProvideInputRequest with input data
        """
        # DEBUG: Log incoming HITL input
        with open("/srv/kodosumi/data/sumi_debug.log", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] provide_input: {fid}/{lid}\n")
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] input_data: {json.dumps(data.input_data, default=str)}\n")

        try:
            lock, actor = find_lock(fid, lid)
        except LockNotFound as e:
            return ProvideInputResponse(
                status="error",
                input_hash=None,
            )

        # Check if lock is already released
        if lock.get("result") is not None:
            return ProvideInputResponse(
                status="error",
                input_hash=None,
            )

        # Post to lock endpoint
        target = f"{lock['app_url']}/_lock_/{fid}/{lid}"

        # Fetch schema and convert MIP-003 index arrays to string values
        converted_input = data.input_data
        try:
            async with HTTPXClient() as client:
                schema_resp = await client.get(target, timeout=10.0)
            if schema_resp.status_code == 200:
                elements = schema_resp.json()
                schema = convert_model_to_schema(elements)
                converted_input = convert_mip003_indices_to_values(data.input_data, schema)
        except Exception:
            pass  # Use original input if schema fetch fails

        try:
            async with HTTPXClient() as client:
                resp = await client.post(
                    target,
                    json=converted_input or {},
                    timeout=10.0,
                )

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Lock {lid}: HTTP {resp.status_code}",
                )

            response_data = resp.json()
            result = response_data.get("result")

            # Release the lock via the actor
            import ray
            ray.get(actor.lease.remote(lid, result))

            # Calculate input hash
            input_hash = create_input_hash(data.input_data, f"{fid}:{lid}")

            return ProvideInputResponse(
                input_hash=input_hash,
                signature=input_hash,
            )

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Lock {lid}: {type(e).__name__}: {e}",
            )
