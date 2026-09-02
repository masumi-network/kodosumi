"""
Flow discovery and metadata helpers for the Sumi protocol.

Parses expose meta YAML into MIP-002 flow items, resolves flow ids and
service urls, and checks the availability and input schema of a flow.
"""

import json
import re
from typing import List, Optional

import yaml

from litestar.exceptions import HTTPException, NotFoundException

from kodosumi.helper import HTTPXClient
from kodosumi.service.expose import db
from kodosumi.service.expose.models import ExposeMeta
from kodosumi.service.proxy import LockNotFound, find_lock
from kodosumi.service.sumi.models import (
    AgentPricing, AuthorInfo, AvailabilityResponse, AwaitingInputSchema,
    CapabilityInfo, ExampleOutput, FixedPricing, InputField, InputGroup,
    InputSchemaResponse, LegalInfo, SumiFlowItem, SumiFlowListResponse)
from kodosumi.service.sumi.schema import (
    convert_model_to_schema, create_empty_schema)


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
            resp = await client.get(endpoint_url, timeout=30.0)

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
) -> Optional[AwaitingInputSchema]:
    """
    Fetch and convert input schemas from all pending lock endpoints.

    Returns MIP-003 compliant AwaitingInputSchema with input_groups.
    Each group maps to a Kodosumi lock, and field IDs are prefixed
    with the lock ID (e.g. "lid-1:full_name") so provide_input can
    route values to the correct lock.

    Args:
        job_id: The job/execution ID (fid)
        lock_ids: Set of pending lock IDs

    Returns:
        AwaitingInputSchema with input_groups, or None if no schemas found
    """
    if not lock_ids:
        return None

    groups: List[InputGroup] = []

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

            # Convert to MIP-003 schema
            input_schema = convert_model_to_schema(elements)

            # Prefix field IDs with lock ID for provide_input routing
            prefixed_fields: List[InputField] = []
            if input_schema.input_data:
                for field in input_schema.input_data:
                    prefixed_fields.append(InputField(
                        id=f"{lid}:{field.id}",
                        type=field.type,
                        name=field.name,
                        data=field.data,
                        validations=field.validations,
                    ))

            groups.append(InputGroup(
                id=lid,
                title=lock.get("name"),
                input_data=prefixed_fields if prefixed_fields else None,
            ))

        except Exception:
            continue

    if not groups:
        return None

    return AwaitingInputSchema(input_groups=groups)
