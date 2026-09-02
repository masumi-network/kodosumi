"""
Sumi Protocol Controller - MIP-002/MIP-003 compliant endpoints.

Provides discovery, availability, and job management for external systems.
"""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)
from litestar import Controller, get, post, Request
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException, NotAuthorizedException
import ray

from kodosumi.const import DB_FILE, SLEEP
from kodosumi.helper import HTTPXClient
from kodosumi.service.proxy import LockNotFound, find_lock
from kodosumi.service.sumi.hash import create_input_hash
from kodosumi.service.sumi.models import (
    AvailabilityResponse, InputSchemaResponse, JobStatusResponse,
    MIP003ProvideInputRequest, ProvideInputResponse, StartJobErrorResponse,
    StartJobRequest, SumiFlowItem, SumiFlowListResponse)
from kodosumi.service.sumi.schema import (
    convert_model_to_schema, convert_mip003_indices_to_values)
from kodosumi.service.jwt import (
    parse_token, sumi_network_guard, sumi_job_network_guard)

# The helpers below stay importable from this module because the flow
# discovery, job and lock code moved out of it into sibling modules.
from kodosumi.service.sumi.flows import (
    _build_flow_id, _build_sumi_url, _check_availability,
    _extract_alive_metas, _extract_result_string, _fetch_input_schema,
    _fetch_lock_input_schemas, _format_service_id, _get_alive_flows,
    _get_meta_entry, _get_meta_name, _is_expose_available, _meta_to_flow_item,
    _paginate_flows, _parse_agent_pricing, _parse_author, _parse_capability,
    _parse_example_output, _parse_legal, _parse_meta_data, _sanitize_name,
    _url_to_name, _validate_path_param)
from kodosumi.service.sumi.jobs import (
    _get_job_status_from_db, _heal_agent_identifier, _submit_job)
from kodosumi.service.sumi.locks import SumiLockControl

# User identifier for jobs started via Sumi protocol
# SUMI_USER = "_sumi_"

# Pagination limits
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 10


class SumiControl(Controller):
    """
    Sumi Protocol Controller.

    Provides MIP-002/MIP-003 compliant endpoints for service discovery
    and management.
    """

    path = "/sumi"
    tags = ["Sumi Protocol"]

    @get(
        "",
        summary="List all services",
        description="List all available services. Use 'expose' query param to filter by expose. "
        "Returns MIP-002 compliant metadata. Paginated by offset. "
        "Unauthenticated requests only see services with network (blockchain) auth.",
        operation_id="sumi_list",
        opt={"no_auth": True},
    )
    async def list_flows(
        self,
        state: State,
        request: Request,
        expose: Optional[str] = None,
        pp: int = DEFAULT_PAGE_SIZE,
        offset: Optional[str] = None,
    ) -> SumiFlowListResponse:
        """
        List services, optionally filtered by expose.

        Args:
            expose: Filter by expose name (optional)
            pp: Page size (items per page, max 100)
            offset: Last item ID from previous page (cursor-based pagination)

        Note:
            Unauthenticated requests only see services where network is set
            (blockchain-based authentication). Authenticated requests see all.
        """
        pp = max(1, min(pp, MAX_PAGE_SIZE))
        app_server = state["settings"].sumi_address

        # Check if user is authenticated (don't fail if not)
        is_authenticated = False
        try:
            parse_token(request)
            is_authenticated = True
        except NotAuthorizedException:
            pass

        # Validate expose filter if provided
        expose_filter = _validate_path_param(expose, "expose") if expose else None

        # Get flows (filtered or all)
        all_flows = await _get_alive_flows(app_server, expose_filter)

        # Filter by network if not authenticated
        if not is_authenticated:
            all_flows = [
                (name, net, meta, srv)
                for name, net, meta, srv in all_flows
                if net is not None
            ]

        # Convert to SumiFlowItem
        items = [
            _meta_to_flow_item(exp_name, exp_net, meta, srv)
            for exp_name, exp_net, meta, srv in all_flows
        ]
        items.sort(key=lambda x: x.id)

        # Apply pagination
        return _paginate_flows(items, pp, offset)

    @get(
        "/{expose_name:str}",
        summary="Get root service or service metadata",
        description="Get MIP-002 compliant metadata for the root service of an expose.",
        operation_id="sumi_get_root_service",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def get_root_service(
        self,
        state: State,
        expose_name: str,
    ) -> SumiFlowItem:
        """Get metadata for the root service (endpoint "/") of an expose."""
        expose_name = _validate_path_param(expose_name, "expose_name")
        app_server = state["settings"].sumi_address

        row, meta = await _get_meta_entry(expose_name, "")
        expose_network = row.get("network")

        return _meta_to_flow_item(expose_name, expose_network, meta, app_server)

    @get(
        "/{expose_name:str}/{meta_name:str}",
        summary="Get service metadata",
        description="Get full MIP-002 compliant metadata for a specific service.",
        operation_id="sumi_get_service",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def get_service_detail(
        self,
        state: State,
        expose_name: str,
        meta_name: str,
    ) -> SumiFlowItem:
        """Get full MIP-002 metadata for a specific service."""
        expose_name = _validate_path_param(expose_name, "expose_name")
        meta_name = _validate_path_param(meta_name, "meta_name")
        app_server = state["settings"].sumi_address

        row, meta = await _get_meta_entry(expose_name, meta_name)
        expose_network = row.get("network")

        return _meta_to_flow_item(expose_name, expose_network, meta, app_server)

    @get(
        "/{expose_name:str}/{meta_name:str}/availability",
        summary="Check service availability",
        description="MIP-003 compliant availability check for a specific service. "
        "Performs a HEAD request to the Ray Serve endpoint to verify availability.",
        operation_id="sumi_availability",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def check_availability(
        self,
        state: State,
        expose_name: str,
        meta_name: str,
    ) -> AvailabilityResponse:
        """
        Check if a service is available.

        Performs a HEAD request to the Ray Serve endpoint to verify the service
        is actually responding.

        Args:
            expose_name: Name of the expose
            meta_name: Name (slug) of the meta entry
        """
        # Validate path parameters
        expose_name = _validate_path_param(expose_name, "expose_name")
        meta_name = _validate_path_param(meta_name, "meta_name")

        ray_serve_address = state["settings"].RAY_SERVE_ADDRESS
        return await _check_availability(expose_name, meta_name, ray_serve_address)

    @get(
        "/{expose_name:str}/availability",
        summary="Check root service availability",
        description="MIP-003 compliant availability check for root service.",
        operation_id="sumi_root_availability",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def check_root_availability(
        self,
        state: State,
        expose_name: str,
    ) -> AvailabilityResponse:
        """Check if root service is available."""
        expose_name = _validate_path_param(expose_name, "expose_name")
        ray_serve_address = state["settings"].RAY_SERVE_ADDRESS
        return await _check_availability(expose_name, "", ray_serve_address)

    @get(
        "/{expose_name:str}/input_schema",
        summary="Get root service input schema",
        description="MIP-003 compliant input schema for root service.",
        operation_id="sumi_root_input_schema",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def get_root_input_schema(
        self,
        state: State,
        expose_name: str,
    ) -> InputSchemaResponse:
        """Get input schema for root service."""
        expose_name = _validate_path_param(expose_name, "expose_name")
        _, meta = await _get_meta_entry(expose_name, "")
        ray_serve_address = state["settings"].RAY_SERVE_ADDRESS
        return await _fetch_input_schema(ray_serve_address, meta)

    async def _start_job(self,
                         state: State,
                         expose_name: str,
                         meta_name: str,
                         data: StartJobRequest,
                         request: Request
    ) -> Union[JobStatusResponse, StartJobErrorResponse]:
        expose_name = _validate_path_param(expose_name, "expose_name")
        row, meta = await _get_meta_entry(expose_name, meta_name)
        network = row.get("network") or "Preprod"
        app_server = state["settings"].sumi_address
        ray_serve_address = state["settings"].RAY_SERVE_ADDRESS
        return await _submit_job(expose_name, meta_name, meta, network, data, app_server, ray_serve_address, request, state)

    @post(
        "/{expose_name:str}/start_job",
        summary="Start job on root service",
        description="MIP-003 compliant job initiation for root service.",
        operation_id="sumi_root_start_job",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def start_root_job(
        self,
        state: State,
        expose_name: str,
        data: StartJobRequest,
        request: Request,
    ) -> Union[JobStatusResponse, StartJobErrorResponse]:
        """Start a job on root service."""
        return await self._start_job(state, expose_name, "", data, request)

    @get(
        "/{expose_name:str}/{meta_name:str}/input_schema",
        summary="Get input schema",
        description="MIP-003 compliant input schema for job initiation.",
        operation_id="sumi_input_schema",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def get_input_schema(
        self,
        state: State,
        expose_name: str,
        meta_name: str,
    ) -> InputSchemaResponse:
        """Get MIP-003 input schema for a service."""
        meta_name = _validate_path_param(meta_name, "meta_name")
        _, meta = await _get_meta_entry(expose_name, meta_name)
        ray_serve_address = state["settings"].RAY_SERVE_ADDRESS
        return await _fetch_input_schema(ray_serve_address, meta)

    @post(
        "/{expose_name:str}/{meta_name:str}/start_job",
        summary="Start a new job",
        description="MIP-003 compliant job initiation. Starts an execution "
        "and returns job status (identical to /status/{job_id} response).",
        operation_id="sumi_start_job",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def start_job(
        self,
        state: State,
        expose_name: str,
        meta_name: str,
        data: StartJobRequest,
        request: Request,
    ) -> Union[JobStatusResponse, StartJobErrorResponse]:
        """Start a new job execution."""
        meta_name = _validate_path_param(meta_name, "meta_name")
        return await self._start_job(state, expose_name, meta_name, data, request)

    async def _get_job_status_impl(
        self,
        state: State,
        job_id: str,
    ) -> JobStatusResponse:
        """Internal implementation for job status retrieval."""
        exec_dir = Path(state["settings"].EXEC_DIR)

        # Search for the job across all user directories
        db_file = None
        for user_dir in exec_dir.iterdir():
            if not user_dir.is_dir():
                continue
            potential_db = user_dir / job_id / DB_FILE
            if potential_db.exists():
                db_file = potential_db
                break

        if not db_file:
            # Wait briefly in case job is still initializing
            await asyncio.sleep(SLEEP)
            for user_dir in exec_dir.iterdir():
                if not user_dir.is_dir():
                    continue
                potential_db = user_dir / job_id / DB_FILE
                if potential_db.exists():
                    db_file = potential_db
                    break

        if not db_file:
            raise NotFoundException(detail=f"Job '{job_id}' not found")

        # Get status from database
        conn = sqlite3.connect(str(db_file), isolation_level=None)
        conn.execute('pragma journal_mode=wal;')
        conn.execute('pragma synchronous=normal;')
        conn.execute('pragma read_uncommitted=true;')

        try:
            status_data, pending_locks = await _get_job_status_from_db(conn, job_id)
        finally:
            conn.close()

        # Fetch input schemas when awaiting_input (MIP-003 status)
        if status_data.status == "awaiting_input" and pending_locks:
            awaiting_schema = await _fetch_lock_input_schemas(job_id, pending_locks)
            return status_data.model_copy(
                update={"input_schema": awaiting_schema})

        return status_data

    @get(
        "/{expose_name:str}/status/{job_id:str}",
        summary="Get root job status (path parameter)",
        description="Job status retrieval using path parameter for root service. "
        "Returns current status and result if completed.",
        operation_id="sumi_root_job_status_path",
        opt={"no_auth": True},
        guards=[sumi_job_network_guard],
    )
    async def get_root_job_status(
        self,
        state: State,
        expose_name: str,
        job_id: str,
    ) -> JobStatusResponse:
        """Get job status using path parameter for root service."""
        return await self._get_job_status_impl(state, job_id)

    @get(
        "/{expose_name:str}/status",
        summary="Get root job status (MIP-003)",
        description="MIP-003 compliant job status retrieval using query parameter. "
        "Returns current status and result if completed.",
        operation_id="sumi_root_job_status",
        opt={"no_auth": True},
    )
    async def get_root_job_status_query(
        self,
        state: State,
        expose_name: str,
        job_id: str,
    ) -> JobStatusResponse:
        """Get job status using query parameter (MIP-003 compliant) for root service."""
        return await self._get_job_status_impl(state, job_id)

    @get(
        "/{expose_name:str}/{meta_name:str}/status/{job_id:str}",
        summary="Get job status (path parameter)",
        description="Job status retrieval using path parameter. Returns current "
        "status and result if completed.",
        operation_id="sumi_job_status_path",
        opt={"no_auth": True},
        guards=[sumi_job_network_guard],
    )
    async def get_job_status(
        self,
        state: State,
        expose_name: str,
        meta_name: str,
        job_id: str,
    ) -> JobStatusResponse:
        """Get job status using path parameter."""
        return await self._get_job_status_impl(state, job_id)

    @get(
        "/{expose_name:str}/{meta_name:str}/status",
        summary="Get job status (MIP-003)",
        description="MIP-003 compliant job status retrieval using query parameter. "
        "Returns current status and result if completed.",
        operation_id="sumi_job_status",
        opt={"no_auth": True},
    )
    async def get_job_status_query(
        self,
        state: State,
        expose_name: str,
        meta_name: str,
        job_id: str,
    ) -> JobStatusResponse:
        """Get job status using query parameter (MIP-003 compliant)."""
        return await self._get_job_status_impl(state, job_id)

    async def _provide_input_impl(
        self,
        state: State,
        data: MIP003ProvideInputRequest,
    ) -> ProvideInputResponse:
        """
        MIP-003 provide_input implementation.

        Parses lock-prefixed field IDs from input_data, groups by lock ID,
        and releases each lock individually via Kodosumi core.

        Only affects Sumi layer — core lock mechanism unchanged.
        """
        fid = data.job_id

        # DEBUG: Log incoming provide_input request
        with open("/srv/kodosumi/data/sumi_debug.log", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] provide_input (MIP-003): job_id={fid}\n")
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] input_schema_hash: {data.input_schema_hash}\n")
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] input_data: {json.dumps(data.input_data, default=str)}\n")

        if not data.input_data:
            return ProvideInputResponse(status="error", input_hash=None)

        # Split keys by lock ID prefix: "lid:field_name" → {lid: {field_name: value}}
        locks_data: dict = {}
        for key, value in data.input_data.items():
            if ":" not in key:
                # No prefix — cannot determine lock, skip
                continue
            lid, field_id = key.split(":", 1)
            locks_data.setdefault(lid, {})[field_id] = value

        if not locks_data:
            with open("/srv/kodosumi/data/sumi_debug.log", "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] provide_input ERROR: no lock-prefixed keys found\n")
            return ProvideInputResponse(status="error", input_hash=None)

        # DEBUG: Log parsed lock groups
        with open("/srv/kodosumi/data/sumi_debug.log", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] provide_input locks: {list(locks_data.keys())}\n")

        # Release each lock via Kodosumi core (find_lock + POST + lease)
        errors = []
        for lid, fields in locks_data.items():
            try:
                lock, actor = find_lock(fid, lid)
            except LockNotFound as e:
                # e.lid is None → actor/execution gone (timeout, crash, finished)
                # e.lid is set → actor alive but lock expired or already released
                if e.lid is None:
                    errors.append(f"Job {fid} is no longer running (lock may have expired)")
                else:
                    errors.append(f"Lock {lid} not found (expired or already released)")
                continue

            if lock.get("result") is not None:
                errors.append(f"Lock {lid} already released")
                continue

            # Fetch schema and convert MIP-003 values (indices, booleans, defaults)
            target = f"{lock['app_url']}/_lock_/{fid}/{lid}"
            converted_fields = fields
            try:
                async with HTTPXClient() as client:
                    schema_resp = await client.get(target, timeout=10.0)
                if schema_resp.status_code == 200:
                    elements = schema_resp.json()
                    schema = convert_model_to_schema(elements)
                    converted_fields = convert_mip003_indices_to_values(fields, schema)
            except Exception:
                pass  # Use original fields if schema fetch fails

            # POST to lock endpoint
            try:
                async with HTTPXClient() as client:
                    resp = await client.post(
                        target,
                        json=converted_fields or {},
                        timeout=10.0,
                    )

                if resp.status_code != 200:
                    errors.append(f"Lock {lid}: HTTP {resp.status_code}")
                    continue

                response_data = resp.json()
                result = response_data.get("result")

                # Release the lock via the actor
                ray.get(actor.lease.remote(lid, result))

            except Exception as e:
                errors.append(f"Lock {lid}: {type(e).__name__}: {e}")

        # DEBUG: Log result
        with open("/srv/kodosumi/data/sumi_debug.log", "a") as f:
            status = "error" if errors else "success"
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] provide_input result: {status}\n")
            if errors:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] provide_input errors: {errors}\n")

        if errors and len(errors) == len(locks_data):
            # All locks failed — return HTTP 400
            raise HTTPException(
                status_code=400,
                detail="; ".join(errors),
            )

        # Compute input hash over original input_data (MIP-003/MIP-004)
        input_hash = create_input_hash(data.input_data, fid)
        return ProvideInputResponse(input_hash=input_hash, signature=input_hash)

    @post(
        "/{expose_name:str}/provide_input",
        summary="Provide input to root service (MIP-003)",
        description="MIP-003 compliant provide_input. Sends additional input "
        "when job is in awaiting_input status. Field IDs must be prefixed "
        "with lock group ID (e.g. 'lock-id-1:field_name').",
        operation_id="sumi_root_provide_input",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def provide_input_root(
        self,
        state: State,
        expose_name: str,
        data: MIP003ProvideInputRequest,
    ) -> ProvideInputResponse:
        """Provide input for root service."""
        return await self._provide_input_impl(state, data)

    @post(
        "/{expose_name:str}/{meta_name:str}/provide_input",
        summary="Provide input (MIP-003)",
        description="MIP-003 compliant provide_input. Sends additional input "
        "when job is in awaiting_input status. Field IDs must be prefixed "
        "with lock group ID (e.g. 'lock-id-1:field_name').",
        operation_id="sumi_provide_input_mip003",
        opt={"no_auth": True},
        guards=[sumi_network_guard],
    )
    async def provide_input_named(
        self,
        state: State,
        expose_name: str,
        meta_name: str,
        data: MIP003ProvideInputRequest,
    ) -> ProvideInputResponse:
        """Provide input for named service."""
        return await self._provide_input_impl(state, data)
