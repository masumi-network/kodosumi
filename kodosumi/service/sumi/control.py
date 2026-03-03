"""
Sumi Protocol Controller - MIP-002/MIP-003 compliant endpoints.

Provides discovery, availability, and job management for external systems.
"""

import asyncio
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import List, Literal, Optional, Union

import yaml
from litestar import Controller, get, post, Request
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException, NotAuthorizedException
import ray

from kodosumi import dtypes
from kodosumi.const import (
    DB_FILE, KODOSUMI_LAUNCH, NAMESPACE, SLEEP, STATUS_END, STATUS_ERROR, 
    STATUS_PAYMENT, ANNONYMOUS)
from kodosumi.helper import HTTPXClient, ProxyRequest, proxy_forward
from kodosumi.service.expose import db
from kodosumi.service.expose.models import ExposeMeta
from kodosumi.service.proxy import LockNotFound, find_lock
from kodosumi.service.sumi.hash import create_input_hash
from kodosumi.service.sumi.models import (
    AgentPricing, AuthorInfo, AvailabilityResponse, CapabilityInfo, 
    ExampleOutput, FixedPricing, InputSchemaResponse, JobStatusResponse, 
    LegalInfo, LockInputSchema, LockSchemaResponse, ProvideInputRequest, 
    ProvideInputResponse, StartJobErrorResponse, StartJobRequest, SumiFlowItem, 
    SumiFlowListResponse) 
from kodosumi.service.sumi.schema import (
    convert_model_to_schema, create_empty_schema)
from kodosumi.service.jwt import (
    parse_token, sumi_network_guard, sumi_job_network_guard)

# User identifier for jobs started via Sumi protocol
# SUMI_USER = "_sumi_"

# Pagination limits
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 10


def _parse_meta_data(data_yaml: Optional[str]) -> dict:
    """Parse the meta.data YAML field into a dict."""
    if not data_yaml:
        return {}
    try:
        return yaml.safe_load(data_yaml) or {}
    except yaml.YAMLError:
        return {}


def _extract_result_string(result_dict) -> Optional[str]:
    """
    Extract string result from various response formats.

    MIP-003 requires result to be a string. This function handles all possible
    formats from the monitor database and always returns a string or None.

    Handles:
    - {"Markdown": {"body": "..."}} → "..."
    - {"HTML": {"body": "..."}} → "..."
    - {"Text": {"body": "..."}} → "..."
    - {"dict": {...}} → JSON string
    - Any other dict → JSON string
    - Plain string → as-is
    - None → None
    """
    if result_dict is None:
        return None

    if isinstance(result_dict, str):
        return result_dict

    if isinstance(result_dict, dict):
        # Handle DynamicModel wrapper: {"dict": {...}} or {"type": "dict", "dict": {...}}
        if "dict" in result_dict and isinstance(result_dict.get("dict"), dict):
            result_dict = result_dict["dict"]

        # Try known response types (Markdown, HTML, Text)
        for response_type in ("Markdown", "HTML", "Text"):
            if response_type in result_dict:
                body = result_dict[response_type]
                if isinstance(body, dict):
                    return body.get("body", json.dumps(body))
                return str(body)

        # Fallback: serialize entire dict as JSON string
        return json.dumps(result_dict)

    # Any other type: convert to string
    return str(result_dict)


# Regex pattern for valid path parameter names
_VALID_NAME_PATTERN = re.compile(r'^[a-z0-9][a-z0-9\-_]*$')


def _validate_path_param(name: str, param_name: str = "name") -> str:
    """
    Validate a path parameter for URL safety.

    Rules:
    - Alphanumeric characters only (a-z, 0-9)
    - Hyphens (-) and underscores (_) allowed (not at start)
    - Must be lowercase
    - No whitespace or special characters

    Args:
        name: The path parameter value to validate
        param_name: Name of the parameter (for error messages)

    Returns:
        The validated name (lowercase)

    Raises:
        HTTPException: If validation fails (400 Bad Request)
    """
    if not name:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {param_name}: cannot be empty"
        )

    # Convert to lowercase
    name_lower = name.lower()

    if not _VALID_NAME_PATTERN.match(name_lower):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {param_name} '{name}': must contain only lowercase "
                   f"alphanumeric characters, hyphens, and underscores"
        )

    return name_lower


def _sanitize_name(name: str) -> str:
    """
    Sanitize name for use as URL slug identifier.

    Pruning rules:
    - Only alphanumeric characters, hyphens (-), and underscores (_) allowed
    - Whitespace replaced with hyphens
    - All other characters removed
    - Lowercase for consistency
    """
    # Replace whitespace with hyphens
    result = re.sub(r'\s+', '-', name)
    # Remove any character that is not alphanumeric, hyphen, or underscore
    result = re.sub(r'[^a-zA-Z0-9\-_]', '', result)
    # Convert to lowercase for URL consistency
    result = result.lower()
    # Remove consecutive hyphens
    result = re.sub(r'-+', '-', result)
    # Strip leading/trailing hyphens
    result = result.strip('-')
    return result or 'unnamed'


def _url_to_name(meta_url: str, expose_name: Optional[str] = None) -> str:
    """
    Generate meta name from meta.url endpoint.

    The URL has format: /{route_prefix}/{endpoint}
    Since route_prefix == expose.name, we extract just the endpoint part.

    When the endpoint is "/" (URL only contains expose name), returns empty string
    to indicate root endpoint.

    Examples:
        "/my-agent/process" -> "process"
        "/my-agent" with expose_name="my-agent" -> "" (root)
        "/stage/" with expose_name="stage" -> "" (root)
        "/deep/nested/path" -> "path"
    """
    parts = meta_url.strip("/").split("/")
    if not parts or (len(parts) == 1 and not parts[0]):
        return ""
    endpoint = parts[-1]
    # If single element matches expose name, this is root endpoint
    if expose_name and len(parts) == 1 and endpoint == expose_name:
        return ""
    return _sanitize_name(endpoint)


def _build_sumi_url(app_server: str, parent: str, meta_name: str) -> str:
    """
    Build the Sumi protocol endpoint URL.

    For root endpoints (meta_name=""), returns /sumi/{parent}
    For named endpoints, returns /sumi/{parent}/{name}
    """
    app_server = app_server.rstrip("/")
    if meta_name:
        return f"{app_server}/sumi/{parent}/{meta_name}"
    return f"{app_server}/sumi/{parent}"




def _parse_agent_pricing(data: dict) -> List[AgentPricing]:
    """Parse agentPricing from meta data dict."""
    pricing_list = data.get("agentPricing", [])
    if not pricing_list:
        # Default pricing if not specified
        return [AgentPricing(
            pricingType="Fixed",
            fixedPricing=[FixedPricing(amount="0", unit="lovelace")]
        )]

    result = []
    for p in pricing_list:
        fixed_pricing = []
        for fp in p.get("fixedPricing", []):
            fixed_pricing.append(FixedPricing(
                amount=str(fp.get("amount", "0")),
                unit=fp.get("unit", "lovelace")
            ))
        result.append(AgentPricing(
            pricingType=p.get("pricingType", "Fixed"),
            fixedPricing=fixed_pricing
        ))
    return result


def _parse_author(data: dict) -> Optional[AuthorInfo]:
    """Parse author from meta data dict."""
    author_data = data.get("author")
    if not author_data:
        return None
    if isinstance(author_data, dict):
        return AuthorInfo(
            name=author_data.get("name"),
            contact_email=author_data.get("contact_email"),
            contact_other=author_data.get("contact_other"),
            organization=author_data.get("organization"),
        )
    return None


def _parse_capability(data: dict) -> Optional[CapabilityInfo]:
    """Parse capability from meta data dict."""
    cap_data = data.get("capability")
    if not cap_data or not isinstance(cap_data, dict):
        return None
    name = cap_data.get("name")
    version = cap_data.get("version")
    if name and version:
        return CapabilityInfo(name=name, version=version)
    return None


def _parse_legal(data: dict) -> Optional[LegalInfo]:
    """Parse legal from meta data dict."""
    legal_data = data.get("legal")
    if not legal_data or not isinstance(legal_data, dict):
        return None
    return LegalInfo(
        privacy_policy=legal_data.get("privacy_policy"),
        terms=legal_data.get("terms"),
        other=legal_data.get("other"),
    )


def _parse_example_output(data: dict) -> Optional[List[ExampleOutput]]:
    """Parse example_output from meta data dict."""
    examples = data.get("example_output", [])
    if not examples:
        return None
    result = []
    for ex in examples:
        if isinstance(ex, dict) and ex.get("name") and ex.get("mime_type") and ex.get("url"):
            result.append(ExampleOutput(
                name=ex["name"],
                mime_type=ex["mime_type"],
                url=ex["url"],
            ))
    return result if result else None


def _build_flow_id(expose_name: str, meta_name: str) -> str:
    """
    Build unique flow identifier.

    For root endpoints (meta_name=""), returns just expose_name.
    For named endpoints, returns {expose_name}/{meta_name}.
    """
    if meta_name:
        return f"{expose_name}/{meta_name}"
    return expose_name


def _meta_to_flow_item(
    expose_name: str,
    expose_network: Optional[str],
    meta: ExposeMeta,
    app_server: str,
) -> SumiFlowItem:
    """Convert ExposeMeta to SumiFlowItem."""
    data = _parse_meta_data(meta.data)
    meta_name = _get_meta_name(meta, expose_name)
    flow_id = _build_flow_id(expose_name, meta_name)
    display = data.get("display") or meta_name or expose_name

    return SumiFlowItem(
        id=flow_id,
        parent=expose_name,
        name=meta_name,
        display=display,
        api_url=_build_sumi_url(app_server, expose_name, meta_name),
        tags=data.get("tags", ["untagged"]) or ["untagged"],
        agentPricing=_parse_agent_pricing(data),
        metadata_version=1,
        description=data.get("description"),
        image=data.get("image"),
        example_output=_parse_example_output(data),
        author=_parse_author(data),
        capability=_parse_capability(data),
        legal=_parse_legal(data),
        network=expose_network,  # None if not set
        state=meta.state or "dead",
    )


def _extract_alive_metas(row: dict, app_server: str) -> List[tuple]:
    """
    Extract alive, enabled meta entries from an expose row.

    Returns list of (expose_name, expose_network, ExposeMeta, app_server) tuples.
    """
    expose_name = row["name"]
    expose_network = row.get("network", "Preprod")
    meta_yaml = row.get("meta")

    if not meta_yaml:
        return []

    result = []
    try:
        meta_list = yaml.safe_load(meta_yaml)
        if meta_list:
            for m in meta_list:
                meta = ExposeMeta(**m)
                if meta.state == "alive" and meta.enabled:
                    result.append((expose_name, expose_network, meta, app_server))
    except (yaml.YAMLError, TypeError, ValueError):
        pass
    return result


def _is_expose_available(row: Optional[dict]) -> bool:
    """Check if expose row is enabled and running."""
    return bool(row and row.get("enabled") and row.get("state") == "RUNNING")


async def _get_alive_flows(
    app_server: str,
    expose_filter: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[tuple]:
    """
    Get alive flows, optionally filtered by expose name.

    Args:
        app_server: App server URL
        expose_filter: If provided, only return flows from this expose
        db_path: Optional database path for testing

    Returns:
        List of (expose_name, expose_network, ExposeMeta, app_server) tuples.

    Raises:
        NotFoundException: If expose_filter is provided but expose not found/available.
    """
    await db.init_database(db_path)

    if expose_filter:
        row = await db.get_expose(expose_filter, db_path)
        if not row:
            raise NotFoundException(detail=f"Expose '{expose_filter}' not found")
        if not row.get("enabled"):
            raise NotFoundException(detail=f"Expose '{expose_filter}' is not enabled")
        if row.get("state") != "RUNNING":
            raise NotFoundException(detail=f"Expose '{expose_filter}' is not running")
        return _extract_alive_metas(row, app_server)

    # Get all exposes
    rows = await db.get_all_exposes(db_path)
    result = []
    for row in rows:
        if _is_expose_available(row):
            result.extend(_extract_alive_metas(row, app_server))
    return result


def _get_meta_name(meta: ExposeMeta, expose_name: Optional[str] = None) -> str:
    """
    Get the technical identifier for a meta entry.

    Always derived from the endpoint in meta.url.
    This is read-only - users can change display name but not the URL.

    Returns empty string for root endpoints (when URL path only contains expose name).
    """
    return _url_to_name(meta.url, expose_name)


def _format_service_id(expose_name: str, meta_name: str) -> str:
    """Format service ID for display in error messages."""
    if meta_name:
        return f"{expose_name}/{meta_name}"
    return expose_name


async def _get_meta_entry(
    expose_name: str,
    meta_name: str,
    db_path: Optional[str] = None,
) -> tuple:
    """
    Get a specific meta entry from an expose.

    Lookup by identifier derived from URL endpoint.
    Use meta_name="" for root endpoints.

    Returns (row, ExposeMeta) tuple.
    Raises NotFoundException if not found.
    """
    await db.init_database(db_path)
    row = await db.get_expose(expose_name, db_path)
    service_id = _format_service_id(expose_name, meta_name)

    if not row:
        raise NotFoundException(detail=f"Expose '{expose_name}' not found")

    if not row.get("enabled"):
        raise NotFoundException(detail=f"Expose '{expose_name}' is not enabled")

    if row.get("state") != "RUNNING":
        raise NotFoundException(detail=f"Expose '{expose_name}' is not running")

    meta_yaml = row.get("meta")
    if not meta_yaml:
        raise NotFoundException(detail=f"Service '{service_id}' not found")

    try:
        meta_list = yaml.safe_load(meta_yaml)
        if meta_list:
            for m in meta_list:
                meta = ExposeMeta(**m)
                # Match by technical identifier (stored or derived)
                # Only return enabled meta entries
                if _get_meta_name(meta, expose_name) == meta_name and meta.enabled:
                    return (row, meta)
    except (yaml.YAMLError, TypeError, ValueError):
        pass

    raise NotFoundException(detail=f"Service '{service_id}' not found")


async def _check_availability(
    expose_name: str,
    meta_name: str,
    ray_serve_address: str,
    db_path: Optional[str] = None,
) -> AvailabilityResponse:
    """
    Check availability of a service via HEAD request to Ray Serve.

    Args:
        expose_name: Name of the expose
        meta_name: Name (slug) of the meta entry (empty for root)
        ray_serve_address: Ray Serve HTTP address
        db_path: Optional database path for testing

    Returns:
        AvailabilityResponse with status and message
    """
    service_id = _format_service_id(expose_name, meta_name)
    try:
        _, meta = await _get_meta_entry(expose_name, meta_name, db_path)
    except NotFoundException:
        return AvailabilityResponse(
            status="unavailable",
            message=f"Service '{service_id}' not found or not available",
        )

    # Build Ray Serve endpoint URL
    endpoint_url = ray_serve_address.rstrip("/") + meta.url

    # Get display name for messages
    data = _parse_meta_data(meta.data)
    display_name = data.get("display") or meta_name or expose_name

    # Perform GET request to verify endpoint is responding
    try:
        async with HTTPXClient() as client:
            resp = await client.get(endpoint_url, timeout=5.0)

        if resp.status_code < 400:
            return AvailabilityResponse(
                status="available",
                message=f"{display_name} is ready to accept jobs",
            )
        else:
            return AvailabilityResponse(
                status="unavailable",
                message=f"Service returned status {resp.status_code}",
            )
    except Exception as e:
        return AvailabilityResponse(
            status="unavailable",
            message=f"Service endpoint is not responding: {type(e).__name__}",
        )


def _paginate_flows(
    items: List[SumiFlowItem],
    page_size: int,
    offset: Optional[str],
) -> SumiFlowListResponse:
    """
    Apply cursor-based pagination to a sorted list of flow items.

    Args:
        items: Sorted list of SumiFlowItem
        page_size: Number of items per page
        offset: ID of last item from previous page (cursor)

    Returns:
        SumiFlowListResponse with paginated items and next offset
    """
    start_idx = 0
    if offset:
        for i, item in enumerate(items):
            if item.id == offset:
                start_idx = i + 1
                break

    end_idx = min(start_idx + page_size, len(items))
    page_items = items[start_idx:end_idx]

    next_offset = None
    if page_items and end_idx < len(items):
        next_offset = page_items[-1].id

    return SumiFlowListResponse(items=page_items, offset=next_offset)


async def _fetch_input_schema(
    ray_serve_address: str,
    meta: ExposeMeta,
) -> InputSchemaResponse:
    """
    Fetch and convert input schema from a service endpoint.

    Args:
        ray_serve_address: Ray Serve HTTP address
        meta: ExposeMeta with endpoint URL

    Returns:
        MIP-003 InputSchemaResponse
    """
    endpoint_url = ray_serve_address.rstrip("/") + meta.url

    try:
        async with HTTPXClient() as client:
            resp = await client.get(endpoint_url, timeout=10.0)

        if resp.status_code != 200:
            return create_empty_schema()

        schema_data = resp.json()
        elements = schema_data.get("elements", [])

        if not elements:
            return create_empty_schema()

        return convert_model_to_schema(elements)

    except Exception:
        return create_empty_schema()


async def _fetch_lock_input_schemas(
    job_id: str,
    lock_ids: set,
) -> List[LockInputSchema]:
    """
    Fetch and convert input schemas from all pending lock endpoints.

    When a job is awaiting, this fetches schemas from all pending locks
    to include in the status response.

    Args:
        job_id: The job/execution ID (fid)
        lock_ids: Set of pending lock IDs

    Returns:
        List of LockInputSchema sorted by lock_id
    """
    if not lock_ids:
        return []

    schemas: List[LockInputSchema] = []

    # Fetch schema from each lock, sorted by lock ID for consistent ordering
    for lid in sorted(lock_ids):
        try:
            lock, _ = find_lock(job_id, lid)
        except LockNotFound:
            continue

        # Skip if lock already released
        if lock.get("result") is not None:
            continue

        # Fetch schema from lock endpoint
        target = f"{lock['app_url']}/_lock_/{job_id}/{lid}"

        try:
            async with HTTPXClient() as client:
                resp = await client.get(target, timeout=10.0)

            if resp.status_code != 200:
                continue

            elements = resp.json()

            # Convert to MIP-003 schema and create LockInputSchema
            input_schema = convert_model_to_schema(elements)
            schemas.append(LockInputSchema(
                lock_id=lid,
                input_data=input_schema.input_data,
                expires_at=lock.get("expires"),
            ))

        except Exception:
            continue

    return schemas


async def _submit_job(
    expose_name: str,
    meta_name: str,
    meta: ExposeMeta,
    network: str,
    data: StartJobRequest,
    app_server: str,
    ray_serve_address: str,
    request: Request,
) -> Union[JobStatusResponse, StartJobErrorResponse]:
    """
    Submit a job to a service endpoint.

    Uses the shared proxy_forward utility to ensure consistent header handling
    with ProxyControl.forward.

    Args:
        expose_name: Name of the expose
        meta_name: Name of the meta entry (empty for root)
        meta: ExposeMeta with endpoint URL
        network: Blockchain network (e.g., "Preprod", "Mainnet")
        data: StartJobRequest with input data
        app_server: App server URL
        ray_serve_address: Ray Serve HTTP address
        request: Original request for user/cookie forwarding

    Returns:
        JobStatusResponse on success, StartJobErrorResponse on failure
    """
    service_id = _format_service_id(expose_name, meta_name)
    input_hash = create_input_hash(data.input_data, data.identifier_from_purchaser)
    meta_data_dict = _parse_meta_data(meta.data)
    agent_identifier = meta_data_dict.get("agentIdentifier")
    endpoint_url = ray_serve_address.rstrip("/") + meta.url
    # Extract application root URL (without entry path) for KODOSUMI_BASE header.
    # Lock routes (/_lock_/) are registered on ServeAPI root, not under entry paths.
    # This mirrors the old behavior: "X-Kodosumi-Base": f"/-/{expose_name}"
    app_root_url = ray_serve_address.rstrip("/") + "/" + expose_name
    started_at = time.time()

    # Extra metadata stored with the job
    # Include network so Runner can initialize payment without DB access
    extra = {
        "identifier_from_purchaser": data.identifier_from_purchaser,
        "input_hash": input_hash,
        "sumi_endpoint": service_id,
        "agentIdentifier": agent_identifier,
        "network": network,
        "raw_input_data": data.input_data,  # Debug: raw input from Sumi start_job
    }

    def _error_response(error_msg: str) -> StartJobErrorResponse:
        return StartJobErrorResponse(error=error_msg)

    try:
        user = request.user
    except Exception:
        user = ANNONYMOUS
    try:
        # Use shared proxy utility with consistent header handling
        # base is the application root URL (without entry path) - lock routes
        # are registered on ServeAPI root, not under entry paths
        proxy_config = ProxyRequest(
            target_url=endpoint_url,
            method="POST",
            user=user,
            base=app_root_url,
            app_url=app_server,
            json_body=data.input_data or {},
            headers=dict(request.headers),
            cookies=dict(request.cookies),
            extra=extra,
            timeout=30.0,
        )

        resp = await proxy_forward(proxy_config)

        if resp.status_code != 200:
            return _error_response(
                f"Service returned HTTP {resp.status_code}: {resp.content.decode()}"
            )

        response_data = resp.json()

        # Check for job ID in response - can come from KODOSUMI_LAUNCH header or response body
        job_id = resp.headers.get(KODOSUMI_LAUNCH) or response_data.get("result") or response_data.get("fid")

        if not job_id:
            errors = response_data.get("errors")
            if errors:
                # Validation errors - format them as error message
                error_msg = "; ".join(f"{k}: {v}" for k, v in errors.items())
                return _error_response(error_msg)
            return _error_response("Service did not return a job ID (fid)")

        # Call prepare on the Runner actor to get payment init data.
        # prepare() is idempotent — if start() already called it,
        # returns the cached result.
        blockchain_id = None
        pay_by_time = None
        submit_result_time = None
        unlock_time = None
        ext_dispute_unlock_time = None
        seller_vkey = None
        try:
            # Use asyncio.to_thread to avoid blocking the event loop
            runner = await asyncio.to_thread(ray.get_actor, job_id, namespace=NAMESPACE)
            prepare_data = await runner.prepare.remote()
            if prepare_data:
                pay_data = prepare_data["pay_data"]
                blockchain_id = prepare_data["blockchain_identifier"]
                pay_by_time = int(pay_data["payByTime"]) if pay_data.get("payByTime") else None
                submit_result_time = int(pay_data["submitResultTime"]) if pay_data.get("submitResultTime") else None
                unlock_time = int(pay_data["unlockTime"]) if pay_data.get("unlockTime") else None
                ext_dispute_unlock_time = int(pay_data["externalDisputeUnlockTime"]) if pay_data.get("externalDisputeUnlockTime") else None
                sc_wallet = pay_data.get("SmartContractWallet") or {}
                seller_vkey = sc_wallet.get("walletVkey")
        except Exception:
            # Actor not found or prepare failed — proceed without payment
            pass

        return JobStatusResponse(
            job_id=job_id,
            status="awaiting_payment" if blockchain_id else "running",
            identifierFromPurchaser=data.identifier_from_purchaser,
            input_hash=input_hash,
            agentIdentifier=agent_identifier,
            blockchainIdentifier=blockchain_id,
            payByTime=pay_by_time,
            submitResultTime=submit_result_time,
            unlockTime=unlock_time,
            externalDisputeUnlockTime=ext_dispute_unlock_time,
            sellerVKey=seller_vkey,
            startedAt=started_at,
            updatedAt=time.time(),
        )

    except Exception as e:
        return _error_response(f"Failed to submit job: {type(e).__name__}: {e}")


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
        app_server = state["settings"].APP_SERVER

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
        app_server = state["settings"].APP_SERVER

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
        app_server = state["settings"].APP_SERVER

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
        app_server = state["settings"].APP_SERVER
        ray_serve_address = state["settings"].RAY_SERVE_ADDRESS
        return await _submit_job(expose_name, meta_name, meta, network, data, app_server, ray_serve_address, request)

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
            input_schemas = await _fetch_lock_input_schemas(job_id, pending_locks)
            # Create new response with input_schema list included
            return JobStatusResponse(
                job_id=status_data.job_id,
                status=status_data.status,
                result=status_data.result,
                error=status_data.error,
                input_schema=input_schemas if input_schemas else None,
                identifierFromPurchaser=status_data.identifierFromPurchaser,
                agentIdentifier=status_data.agentIdentifier,
                startedAt=status_data.startedAt,
                updatedAt=status_data.updatedAt,
                runtime=status_data.runtime,
            )

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


async def _get_job_status_from_db(
    conn: sqlite3.Connection, job_id: str
) -> tuple:
    """
    Query job status from the monitor database.

    Maps Kodosumi status to MIP-003 status.

    Returns:
        Tuple of (JobStatusResponse, pending_lock_ids) where pending_lock_ids
        is a set of lock IDs that are awaiting input.
    """
    cursor = conn.cursor()

    # Get timestamps
    cursor.execute("""
        SELECT MIN(timestamp), MAX(timestamp) FROM monitor
    """)
    first_ts, last_ts = cursor.fetchone()

    # Get current status
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'status'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    kodo_status = row[0] if row else None

    # Get final result (MIP-003 requires string)
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'final'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    final_result = None
    if row:
        try:
            parsed = dtypes.DynamicModel.model_validate_json(row[0])
            final_result = _extract_result_string(parsed.model_dump())
        except Exception:
            # Fallback: return raw string on parse failure
            final_result = row[0] if isinstance(row[0], str) else str(row[0])

    # Get error if any
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'error'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    error_msg = row[0] if row else None

    # Get meta for identifier_from_purchaser
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'meta'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    identifier = None
    agent_identifier = None
    if row:
        try:
            meta_data = dtypes.DynamicModel.model_validate_json(row[0])
            meta_dict = meta_data.root.get("dict", {})
            extra = meta_dict.get("extra", {})
            if isinstance(extra, dict):
                identifier = extra.get("identifier_from_purchaser")
                agent_identifier = extra.get("agentIdentifier")
        except Exception:
            pass

    # Get payment data from EVENT_PAYMENT records
    blockchain_id = None
    pay_by_time = None
    submit_result_time = None
    unlock_time = None
    ext_dispute_unlock_time = None
    seller_vkey = None
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'payment'
        ORDER BY timestamp ASC
    """)
    for (msg,) in cursor.fetchall():
        try:
            pay_event = dtypes.DynamicModel.model_validate_json(msg)
            pay_dict = pay_event.root.get("dict", {})
            if pay_dict.get("step") == "initialized":
                blockchain_id = pay_dict.get("blockchainIdentifier")
                pd = pay_dict.get("pay_data", {})
                pay_by_time = int(pd["payByTime"]) if pd.get("payByTime") else None
                submit_result_time = int(pd["submitResultTime"]) if pd.get("submitResultTime") else None
                unlock_time = int(pd["unlockTime"]) if pd.get("unlockTime") else None
                ext_dispute_unlock_time = int(pd["externalDisputeUnlockTime"]) if pd.get("externalDisputeUnlockTime") else None
                sc_wallet = pd.get("SmartContractWallet") or {}
                seller_vkey = sc_wallet.get("walletVkey")
        except Exception:
            pass

    # Check for locks (awaiting_input)
    cursor.execute("""
        SELECT kind, message FROM monitor
        WHERE kind IN ('lock', 'lease')
        ORDER BY timestamp ASC
    """)
    locks = set()
    for kind, msg in cursor.fetchall():
        try:
            d = dtypes.DynamicModel.model_validate_json(msg)
            lid = d.root["dict"]["lid"]
            if kind == "lock":
                locks.add(lid)
            else:
                locks.discard(lid)
        except Exception:
            pass

    # Map Kodosumi status to MIP-003 status
    # Kodosumi statuses: starting, running, payment, finished, error
    # MIP-003 statuses: awaiting_payment, awaiting_input, running, completed, failed
    #
    # Runner emits EVENT_STATUS with these values:
    # - STATUS_PAYMENT ("payment") when awaiting payment
    # - STATUS_RUNNING ("running") after payment confirmed (main.py:303)
    # - STATUS_END ("finished") on success
    # - STATUS_ERROR ("error") on failure
    mip_status: Literal[
        "awaiting_payment", "awaiting_input", "running", "completed", "failed"
    ]
    if kodo_status == STATUS_END:
        mip_status = "completed"
    elif kodo_status == STATUS_ERROR:
        mip_status = "failed"
    elif kodo_status == STATUS_PAYMENT:
        mip_status = "awaiting_payment"
    elif locks:
        # Pending locks indicate awaiting human input
        mip_status = "awaiting_input"
    else:
        mip_status = "running"

    # Calculate runtime
    runtime = None
    if first_ts and last_ts:
        runtime = last_ts - first_ts

    response = JobStatusResponse(
        job_id=job_id,
        status=mip_status,
        result=final_result if mip_status == "completed" else None,
        error=error_msg if mip_status == "failed" else None,
        input_schema=None,  # Populated by caller when awaiting_input
        identifierFromPurchaser=identifier,
        agentIdentifier=agent_identifier,
        blockchainIdentifier=blockchain_id,
        payByTime=pay_by_time,
        submitResultTime=submit_result_time,
        unlockTime=unlock_time,
        externalDisputeUnlockTime=ext_dispute_unlock_time,
        sellerVKey=seller_vkey,
        startedAt=first_ts,
        updatedAt=last_ts,
        runtime=runtime,
    )
    return response, locks


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

        try:
            async with HTTPXClient() as client:
                resp = await client.post(
                    target,
                    json=data.input_data or {},
                    timeout=10.0,
                )

            if resp.status_code != 200:
                return ProvideInputResponse(
                    status="error",
                    input_hash=None,
                )

            response_data = resp.json()
            result = response_data.get("result")

            # Release the lock via the actor
            import ray
            ray.get(actor.lease.remote(lid, result))

            # Calculate input hash
            input_hash = create_input_hash(data.input_data, f"{fid}:{lid}")

            return ProvideInputResponse(
                status="success",
                input_hash=input_hash,
            )

        except Exception:
            return ProvideInputResponse(
                status="error",
                input_hash=None,
            )
