"""
Controller for expose API endpoints.

All endpoints require operator role authentication.
"""

import asyncio
import time
import uuid
from pathlib import Path
from typing import List, Optional

import yaml
import litestar
from litestar import Request, delete, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException, NotFoundException, ValidationException
from litestar.response import Redirect, Stream, Template
from sqlalchemy import select

from kodosumi.dtypes import Role
from kodosumi.helper import HTTPXClient
from kodosumi.service.expose.boot import (
    BootMessage,
    BootStep,
    boot_lock,
    get_ray_serve_address_from_config,
    run_boot_process,
    run_shutdown,
    start_boot_background,
    check_app_running,
    check_endpoint_alive,
    fetch_registered_flows,
    get_expose_name_from_base_url,
    get_path_from_base_url,
    check_fields_match,
    parse_meta_data_yaml,
)
from kodosumi.service.jwt import operator_guard
from kodosumi.service.expose import db
from kodosumi.service.expose.models import (
    ExposeCreate,
    ExposeMeta,
    ExposeResponse,
    meta_to_yaml,
)

# Default serve config
RAY_SERVE_CONFIG = "./data/serve_config.yaml"

DEFAULT_SERVE_CONFIG = """# Kodosumi Ray Serve Configuration
proxy_location: EveryNode

http_options:
  host: 0.0.0.0
  port: 8005

grpc_options:
  port: 9000
  grpc_servicer_functions: []

logging_config:
  encoding: TEXT
  log_level: WARNING
  logs_dir: null
  enable_access_log: true
"""


async def get_ray_serve_status(ray_dashboard: str) -> dict:
    """
    Query Ray Serve API for application statuses.
    Returns dict mapping route_prefix to status.
    """
    url = f"{ray_dashboard}/api/serve/applications/"
    try:
        async with HTTPXClient() as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
            if resp.status_code == 200:
                js = resp.json()
                apps = js.get("applications", {})
                # Map route_prefix to status
                result = {}
                for app_name, app_info in apps.items():
                    route_prefix = app_info.get("route_prefix", f"/{app_name}")
                    result[route_prefix] = app_info.get("status", "UNKNOWN")
                return result
    except Exception:
        pass
    return {}


def ensure_serve_config():
    """Ensure serve_config.yaml exists with defaults."""
    config_path = Path(RAY_SERVE_CONFIG)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(DEFAULT_SERVE_CONFIG)


async def get_username(user_id: str, state: State) -> str:
    """
    Look up username from user ID.

    Args:
        user_id: UUID string of the user
        state: Litestar state containing session_maker_class

    Returns:
        Username string, or the user_id if lookup fails
    """
    try:
        session = state["session_maker_class"]()
        async with session:
            query = select(Role).where(Role.id == uuid.UUID(user_id))
            result = await session.execute(query)
            role = result.scalar_one_or_none()
            if role:
                return role.name
    except Exception:
        pass
    return user_id  # Fallback to ID if lookup fails


class ExposeControl(litestar.Controller):
    """Controller for expose management endpoints."""

    path = "/expose"
    tags = ["Expose"]
    guards = [operator_guard]

    @get(
        "",
        summary="List all expose items",
        description="Retrieve all expose items from the database.",
        operation_id="expose_list",
    )
    async def list_exposes(self, state: State) -> List[ExposeResponse]:
        """Get all expose items."""
        await db.init_database()
        rows = await db.get_all_exposes()
        return [ExposeResponse.from_db_row(row) for row in rows]

    @get(
        "/{name:str}",
        summary="Get expose item",
        description="Retrieve a single expose item by name.",
        operation_id="expose_get",
    )
    async def get_expose(self, name: str, state: State) -> ExposeResponse:
        """Get a single expose item by name."""
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")
        return ExposeResponse.from_db_row(row)

    @post(
        "",
        summary="Create or update expose item",
        description="Create a new expose item or update an existing one.",
        operation_id="expose_upsert",
    )
    async def upsert_expose(
        self, data: ExposeCreate, state: State
    ) -> ExposeResponse:
        """Create or update an expose item."""
        await db.init_database()
        now = time.time()

        # Validate network against configured Masumi networks
        if data.network:
            valid_networks = state["settings"].masumi_network_names
            if data.network not in valid_networks:
                raise ClientException(
                    detail=f"Unknown network '{data.network}'. "
                           f"Available networks: {valid_networks}",
                    status_code=422,
                )

        # Determine if this is a rename operation
        is_rename = (
            data.original_name
            and data.original_name != data.name
        )

        if is_rename:
            # Rename case: original_name -> name
            # Check if the NEW name already exists (would be a different record)
            existing_new = await db.get_expose(data.name)
            if existing_new:
                raise ClientException(
                    detail=f"An expose with name '{data.name}' already exists.",
                    status_code=409,
                )

            # Validate ETag against the ORIGINAL record
            if data.etag:
                existing_original = await db.get_expose(data.original_name)
                if existing_original:
                    current_etag = str(existing_original["updated"])
                    if data.etag != current_etag:
                        raise ClientException(
                            detail="This record has been modified by another user. "
                                   "Please reload the page and try again.",
                            status_code=409,
                        )

            # Delete the old record (will be recreated with new name)
            await db.delete_expose(data.original_name)
        else:
            # Update case (same name or new record)
            # ETag validation for optimistic concurrency control
            if data.etag:
                existing = await db.get_expose(data.name)
                if existing:
                    current_etag = str(existing["updated"])
                    if data.etag != current_etag:
                        raise ClientException(
                            detail="This record has been modified by another user. "
                                   "Please reload the page and try again.",
                            status_code=409,
                        )

        # Determine state based on actual Ray status
        if not data.bootstrap or not data.bootstrap.strip():
            # No bootstrap config = DRAFT
            state_value = "DRAFT"
        else:
            # Query Ray Serve API for actual status (regardless of enabled flag)
            # If disabled but still running, needs_reboot will flag it
            ray_dashboard = state["settings"].RAY_DASHBOARD
            statuses = await get_ray_serve_status(ray_dashboard)
            route_prefix = f"/{data.name}"
            if route_prefix in statuses:
                state_value = statuses[route_prefix]
            else:
                state_value = "DEAD"

        # Convert meta to YAML for storage
        meta_yaml = meta_to_yaml(data.meta)

        # Upsert
        row = await db.upsert_expose(
            name=data.name,
            display=data.display,
            network=data.network,
            enabled=data.enabled,
            state=state_value,
            heartbeat=now,
            bootstrap=data.bootstrap,
            meta=meta_yaml,
        )

        return ExposeResponse.from_db_row(row)

    @delete(
        "/{name:str}",
        summary="Delete expose item",
        description="Permanently delete an expose item. This action cannot be undone.",
        operation_id="expose_delete",
    )
    async def delete_expose(self, name: str, state: State) -> None:
        """Delete an expose item."""
        await db.init_database()
        deleted = await db.delete_expose(name)
        if not deleted:
            raise NotFoundException(detail=f"Expose '{name}' not found")

    @post(
        "/health",
        summary="Health check all exposes",
        description="Validate all exposes against reality and update state/heartbeat.",
        operation_id="expose_health_all",
    )
    async def health_check_all(self, request: Request, state: State) -> dict:
        """
        Health check all exposes.

        Checks:
        1. App RUNNING status via Ray dashboard
        2. Endpoint alive via HEAD request
        3. Meta fields populated

        Updates expose.state, expose.heartbeat, and all meta state/heartbeats.
        """
        await db.init_database()
        ray_dashboard = state["settings"].RAY_DASHBOARD
        ray_serve_address = get_ray_serve_address_from_config(
            fallback=state["settings"].RAY_SERVE_ADDRESS
        )
        app_server = state["settings"].APP_SERVER
        auth_cookies = dict(request.cookies)

        # Fetch all flows once
        all_flows = await fetch_registered_flows(app_server, auth_cookies)

        # Build flow lookup by path
        flow_by_path = {}
        for flow in all_flows:
            base_url = flow.get("base_url", "")
            url_path = get_path_from_base_url(base_url)
            flow_by_path[url_path] = flow

        # Get all exposes
        rows = await db.get_all_exposes()
        results = []
        now = time.time()

        for row in rows:
            expose_name = row["name"]
            expose_result = await self._check_expose_health(
                expose_name=expose_name,
                row=row,
                ray_dashboard=ray_dashboard,
                ray_serve_address=ray_serve_address,
                flow_by_path=flow_by_path,
                now=now,
            )
            results.append(expose_result)

        # Count how many exposes had state changes
        updated_count = sum(1 for r in results if r.get("state_changed"))

        return {
            "checked": len(results),
            "updated": updated_count,
            "timestamp": now,
            "results": results,
        }

    @post(
        "/{name:str}/health",
        summary="Health check single expose",
        description="Validate a single expose against reality and update state/heartbeat.",
        operation_id="expose_health_single",
    )
    async def health_check_single(
        self, name: str, request: Request, state: State
    ) -> dict:
        """Health check a single expose."""
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        ray_dashboard = state["settings"].RAY_DASHBOARD
        ray_serve_address = get_ray_serve_address_from_config(
            fallback=state["settings"].RAY_SERVE_ADDRESS
        )
        app_server = state["settings"].APP_SERVER
        auth_cookies = dict(request.cookies)

        # Fetch flows
        all_flows = await fetch_registered_flows(app_server, auth_cookies)

        # Build flow lookup by path
        flow_by_path = {}
        for flow in all_flows:
            base_url = flow.get("base_url", "")
            url_path = get_path_from_base_url(base_url)
            flow_by_path[url_path] = flow

        now = time.time()
        result = await self._check_expose_health(
            expose_name=name,
            row=row,
            ray_dashboard=ray_dashboard,
            ray_serve_address=ray_serve_address,
            flow_by_path=flow_by_path,
            now=now,
        )

        return {
            "checked": 1,
            "timestamp": now,
            "results": [result],
        }

    async def _check_expose_health(
        self,
        expose_name: str,
        row: dict,
        ray_dashboard: str,
        ray_serve_address: str,
        flow_by_path: dict,
        now: float,
    ) -> dict:
        """
        Check health of a single expose and update database.

        Returns dict with validation results.
        """
        # Track previous state for change detection
        old_state = row.get("state", "")

        # Parse existing meta
        existing_metas = []
        if row.get("meta"):
            try:
                import yaml
                meta_list = yaml.safe_load(row["meta"])
                if meta_list:
                    existing_metas = [ExposeMeta(**m) for m in meta_list]
            except Exception:
                pass

        # Check app running status
        app_status = await check_app_running(ray_dashboard, expose_name)

        # Determine expose state based on config and Ray status
        bootstrap = row.get("bootstrap", "")
        enabled = row.get("enabled", True)

        if not bootstrap or not bootstrap.strip():
            # No bootstrap config = DRAFT (can't deploy)
            expose_state = "DRAFT"
        elif app_status.valid:
            # Running in Ray = RUNNING (regardless of enabled flag)
            # If disabled but still running, needs_reboot will flag it
            expose_state = "RUNNING"
        elif "not found" in app_status.message.lower():
            # Not deployed in Ray = DEAD
            expose_state = "DEAD"
        else:
            # Deployed but Ray reports issues = UNHEALTHY
            expose_state = "UNHEALTHY"

        # Check each meta entry
        meta_results = []
        updated_metas = []

        for meta in existing_metas:
            # Check endpoint alive
            endpoint_status = await check_endpoint_alive(
                ray_serve_address, meta.url
            )

            # Check fields
            flow_data = flow_by_path.get(meta.url, {})
            meta_data = parse_meta_data_yaml(meta.data)
            fields_status = check_fields_match(meta_data, flow_data)

            # Update meta state
            if endpoint_status.valid:
                meta_state = "alive"
            else:
                meta_state = "dead"

            # Create updated meta (preserve enabled state)
            updated_meta = ExposeMeta(
                url=meta.url,
                data=meta.data,
                enabled=meta.enabled,
                state=meta_state,
                heartbeat=now,
            )
            updated_metas.append(updated_meta)

            meta_results.append({
                "url": meta.url,
                "endpoint": {
                    "valid": endpoint_status.valid,
                    "message": endpoint_status.message,
                },
                "fields": {
                    "valid": fields_status.valid,
                    "message": fields_status.message,
                },
                "state": meta_state,
            })

        # Update database
        if updated_metas:
            meta_yaml = meta_to_yaml(updated_metas)
            if meta_yaml:
                await db.update_expose_meta(expose_name, meta_yaml)

        await db.update_expose_state(expose_name, expose_state, now)

        # Count stats
        alive_count = sum(1 for m in updated_metas if m.state == "alive")
        fields_ok = sum(1 for r in meta_results if r["fields"]["valid"])

        # Detect state change
        state_changed = (old_state != expose_state)

        return {
            "name": expose_name,
            "app": {
                "valid": app_status.valid,
                "message": app_status.message,
            },
            "state": expose_state,
            "state_changed": state_changed,
            "meta_count": len(updated_metas),
            "alive_count": alive_count,
            "fields_ok": fields_ok,
            "meta": meta_results,
        }


class RegistryControl(litestar.Controller):
    """Controller for Masumi Registry integration endpoints."""

    path = "/expose/{name:str}/registry"
    tags = ["Registry"]
    guards = [operator_guard]

    @get(
        "",
        summary="Get registry status for a flow",
        operation_id="registry_status",
    )
    async def get_status(
        self, name: str, state: State, flow_url: Optional[str] = None
    ) -> dict:
        """
        Check Masumi Registry status for a specific flow.

        Reads agentIdentifier/registrationId from the flow's meta YAML
        and queries the Masumi Registry API for current status.
        """
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            return {"registered": False, "error": "No network configured"}

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            return {"registered": False, "error": str(e)}

        # Parse meta to find the flow
        meta_data = self._get_flow_meta(row, flow_url)
        if meta_data is None:
            return {"registered": False, "error": "Flow not found"}

        agent_id = meta_data.get("agentIdentifier")
        reg_id = meta_data.get("registrationId")

        if not agent_id and not reg_id:
            return {"registered": False, "state": "NotRegistered"}

        # Query registry
        from kodosumi.service.expose.registry import get_registration_status
        result = await get_registration_status(
            masumi,
            registration_id=reg_id,
            agent_identifier=agent_id,
        )

        if not result:
            return {
                "registered": False,
                "state": "NotFound",
                "error": "Registration not found in registry",
                "registrationId": reg_id,
                "agentIdentifier": agent_id,
            }

        return {
            "registered": result.get("state") == "RegistrationConfirmed",
            "state": result.get("state", "Unknown"),
            "agentIdentifier": result.get("agentIdentifier"),
            "registrationId": result.get("id"),
            "name": result.get("name"),
            "error": result.get("error"),
            "transaction": result.get("CurrentTransaction"),
        }

    @post(
        "",
        summary="Register agent on Masumi",
        operation_id="registry_register",
    )
    async def register(
        self, name: str, data: dict, state: State
    ) -> dict:
        """
        Register an agent flow on the Masumi on-chain registry.

        Reads display, description, tags, pricing from the flow's meta YAML.
        Requires wallet_vkey and flow_url in the request body.

        Body:
            flow_url: str - Flow URL path (e.g. /myapp/analyze)
            wallet_vkey: str - Selling wallet verification key
            pricing_type: str - "Free" or "Fixed" (optional, reads from YAML if not set)
            amount: float - Human-readable amount (optional, reads from YAML if not set)
            currency: str - "USDM" or "ADA" (optional, reads from YAML if not set)
        """
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            raise ClientException(detail="No network configured for this expose", status_code=422)

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)

        # Validate API connectivity first
        from kodosumi.service.expose.registry import (
            register_agent, pricing_yaml_to_registry, pricing_to_yaml_format,
            update_meta_yaml_field, list_wallets,
        )

        # Quick health check to validate token
        try:
            wallets = await list_wallets(masumi)
        except Exception as e:
            raise ClientException(
                detail=f"Cannot reach Masumi API: {e}. Check KODO_MASUMI configuration.",
                status_code=502,
            )

        if not wallets:
            raise ClientException(
                detail=f"No wallets found for network '{network}'. "
                       "Check KODO_MASUMI token and payment source configuration.",
                status_code=422,
            )

        flow_url = data.get("flow_url", "")
        wallet_vkey = data.get("wallet_vkey", "")

        if not flow_url:
            raise ClientException(detail="flow_url is required", status_code=422)
        if not wallet_vkey:
            raise ClientException(detail="wallet_vkey is required", status_code=422)

        # Validate wallet exists
        valid_vkeys = [w["walletVkey"] for w in wallets]
        if wallet_vkey not in valid_vkeys:
            raise ClientException(
                detail=f"Wallet '{wallet_vkey[:8]}...' not found. Available: {[v[:8] + '...' for v in valid_vkeys]}",
                status_code=422,
            )

        # Parse meta YAML for this flow
        meta_data = self._get_flow_meta(row, flow_url)
        if meta_data is None:
            raise ClientException(detail=f"Flow '{flow_url}' not found", status_code=404)

        if meta_data.get("agentIdentifier"):
            raise ClientException(
                detail="This flow is already registered. Deregister first to re-register.",
                status_code=409,
            )

        # Build registration data from YAML
        display_name = meta_data.get("display", name)
        description = meta_data.get("description", "")
        tags = meta_data.get("tags", [])
        author_data = meta_data.get("author")
        capability_data = meta_data.get("capability")
        legal_data = meta_data.get("legal")

        # Build author dict for registry
        author = None
        if author_data and isinstance(author_data, dict):
            author = {
                "name": author_data.get("name") or "",
                "contactEmail": author_data.get("contact_email") or "",
                "organization": author_data.get("organization") or "",
            }

        # Build capability dict
        capability = None
        if capability_data and isinstance(capability_data, dict):
            capability = {
                "name": capability_data.get("name") or "",
                "version": str(capability_data.get("version", "1.0")),
            }

        # Determine pricing
        pricing_type = data.get("pricing_type")
        amount = data.get("amount")
        currency = data.get("currency")

        if pricing_type and pricing_type != "Free" and amount is not None and currency:
            # Use values from dialog
            yaml_pricing = pricing_to_yaml_format(pricing_type, float(amount), currency, network)
            registry_pricing = pricing_yaml_to_registry(yaml_pricing, network)
        elif meta_data.get("agentPricing"):
            # Use values from YAML
            yaml_pricing = meta_data["agentPricing"]
            registry_pricing = pricing_yaml_to_registry(yaml_pricing, network)
        else:
            raise ClientException(
                detail="No pricing configured. Set pricing_type/amount/currency or add agentPricing to the YAML.",
                status_code=422,
            )

        # Compute apiBaseUrl
        sumi_address = state["settings"].sumi_address.rstrip("/")
        api_base_url = f"{sumi_address}/sumi{flow_url}"

        # Register
        try:
            result = await register_agent(
                masumi=masumi,
                name=display_name,
                description=description,
                api_base_url=api_base_url,
                tags=tags,
                pricing=registry_pricing,
                author=author,
                capability=capability,
                legal=legal_data,
                wallet_vkey=wallet_vkey,
            )
        except RuntimeError as e:
            raise ClientException(detail=str(e), status_code=502)

        registration_id = result.get("id", "")

        # Update meta YAML with registrationId and pricing
        self._update_flow_meta(row, name, flow_url, {
            "registrationId": registration_id,
            "agentPricing": yaml_pricing if pricing_type else meta_data.get("agentPricing"),
        })

        return {
            "success": True,
            "registrationId": registration_id,
            "state": result.get("state", "RegistrationRequested"),
            "agentIdentifier": result.get("agentIdentifier"),
        }

    @post(
        "/poll",
        summary="Poll registry status and update YAML",
        operation_id="registry_poll",
    )
    async def poll(
        self, name: str, data: dict, state: State
    ) -> dict:
        """
        Poll registration status and update meta YAML when confirmed.

        Called periodically by frontend JS after registration.

        Body:
            flow_url: str - Flow URL path
        """
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            return {"error": "No network configured"}

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            return {"error": str(e)}

        flow_url = data.get("flow_url", "")
        meta_data = self._get_flow_meta(row, flow_url)
        if meta_data is None:
            return {"error": "Flow not found"}

        agent_id = meta_data.get("agentIdentifier")
        reg_id = meta_data.get("registrationId")

        if not reg_id and not agent_id:
            return {"state": "NotRegistered"}

        if agent_id:
            return {"state": "RegistrationConfirmed", "agentIdentifier": agent_id}

        # Poll registry
        from kodosumi.service.expose.registry import get_registration_status
        result = await get_registration_status(
            masumi,
            registration_id=reg_id,
            agent_identifier=agent_id,
        )

        if not result:
            return {"state": "Polling", "registrationId": reg_id}

        reg_state = result.get("state", "Unknown")
        new_agent_id = result.get("agentIdentifier")

        # If confirmed, write agentIdentifier to YAML
        if reg_state == "RegistrationConfirmed" and new_agent_id:
            self._update_flow_meta(row, name, flow_url, {
                "agentIdentifier": new_agent_id,
            })

        return {
            "state": reg_state,
            "agentIdentifier": new_agent_id,
            "registrationId": result.get("id"),
            "error": result.get("error"),
            "transaction": result.get("CurrentTransaction"),
        }

    @post(
        "/deregister",
        summary="Deregister agent",
        operation_id="registry_deregister",
    )
    async def deregister(
        self, name: str, data: dict, state: State
    ) -> dict:
        """
        Deregister an agent from the on-chain registry.

        Body:
            flow_url: str - Flow URL path
        """
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            raise ClientException(detail="No network configured", status_code=422)

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)

        flow_url = data.get("flow_url", "")
        meta_data = self._get_flow_meta(row, flow_url)
        if meta_data is None:
            raise ClientException(detail=f"Flow '{flow_url}' not found", status_code=404)

        agent_id = meta_data.get("agentIdentifier")
        if not agent_id:
            raise ClientException(detail="No agentIdentifier found — not registered", status_code=422)

        from kodosumi.service.expose.registry import deregister_agent
        try:
            result = await deregister_agent(masumi, agent_id)
        except RuntimeError as e:
            raise ClientException(detail=str(e), status_code=502)

        # Remove agentIdentifier and registrationId from YAML
        self._update_flow_meta(row, name, flow_url, {
            "agentIdentifier": None,
            "registrationId": None,
        })

        return {
            "success": True,
            "state": result.get("state", "DeregistrationRequested"),
        }

    def _get_flow_meta(self, row: dict, flow_url: Optional[str]) -> Optional[dict]:
        """Parse meta YAML and return the data dict for a specific flow URL."""
        if not row.get("meta"):
            return None

        try:
            meta_list = yaml.safe_load(row["meta"])
            if not meta_list:
                return None
        except yaml.YAMLError:
            return None

        for entry in meta_list:
            entry_url = entry.get("url", "")
            if flow_url and entry_url != flow_url:
                continue
            # Parse the data YAML string
            data_str = entry.get("data", "")
            if not data_str:
                return {}
            try:
                parsed = yaml.safe_load(data_str)
                return parsed if isinstance(parsed, dict) else {}
            except yaml.YAMLError:
                return {}

        return None

    def _update_flow_meta(
        self, row: dict, expose_name: str, flow_url: str, updates: dict
    ):
        """Update fields in a flow's meta YAML data and save to DB."""
        if not row.get("meta"):
            return

        try:
            meta_list = yaml.safe_load(row["meta"])
            if not meta_list:
                return
        except yaml.YAMLError:
            return

        updated = False
        for entry in meta_list:
            if entry.get("url") != flow_url:
                continue

            data_str = entry.get("data", "")
            try:
                parsed = yaml.safe_load(data_str) if data_str else {}
                if not isinstance(parsed, dict):
                    parsed = {}
            except yaml.YAMLError:
                parsed = {}

            for key, value in updates.items():
                if value is None:
                    parsed.pop(key, None)
                else:
                    parsed[key] = value

            entry["data"] = yaml.dump(
                parsed,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
            updated = True
            break

        if updated:
            new_meta_yaml = yaml.dump(
                meta_list,
                default_flow_style=False,
                allow_unicode=True,
            )
            # Fire-and-forget DB update
            import asyncio
            asyncio.create_task(db.update_expose_meta(expose_name, new_meta_yaml))


class WalletsControl(litestar.Controller):
    """Controller for wallet listing endpoint."""

    path = "/expose/{name:str}/wallets"
    tags = ["Registry"]
    guards = [operator_guard]

    @get(
        "",
        summary="List wallets for expose network",
        operation_id="registry_wallets",
    )
    async def list_wallets(self, name: str, state: State) -> dict:
        """List available selling wallets for the expose's configured network."""
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            return {"wallets": [], "error": "No network configured. Set network first."}

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            return {"wallets": [], "error": str(e)}

        from kodosumi.service.expose.registry import list_wallets
        try:
            wallets = await list_wallets(masumi)
        except Exception as e:
            return {
                "wallets": [],
                "error": f"Cannot reach Masumi API: {e}. Check KODO_MASUMI token.",
            }

        if not wallets:
            return {
                "wallets": [],
                "error": f"No selling wallets found for network '{network}'. "
                         "Check your Masumi Payment API token and configuration.",
            }

        return {"wallets": wallets, "network": network}


class ExposeUIControl(litestar.Controller):
    """Controller for expose UI pages."""

    path = "/admin/expose"
    tags = ["Expose UI"]
    guards = [operator_guard]

    @get(
        "/",
        summary="Expose main page",
        description="Display the main expose management page.",
        operation_id="expose_main_page",
    )
    async def main_page(self, state: State) -> Template | Redirect:
        """Render the main expose page with card listing."""
        # If boot is in progress, redirect operator to boot screen
        if boot_lock.is_locked:
            return Redirect(path="/admin/expose/boot")

        await db.init_database()
        rows = await db.get_all_exposes()
        items = [ExposeResponse.from_db_row(row) for row in rows]

        # Calculate active/total flows for each item
        for item in items:
            if item.meta:
                total = len(item.meta)
                alive = sum(1 for m in item.meta if m.state == "alive")
                item.flow_stats = f"{alive}/{total}"
                # Stale indicator: only for enabled exposes (some endpoints down)
                # For disabled exposes, needs_reboot covers the "still running" case
                if item.enabled:
                    item.stale = any(m.state != "alive" for m in item.meta)
                else:
                    item.stale = False
            else:
                item.flow_stats = "0/0"
                item.stale = False

            # Gap indicator: target state vs current state
            # - enabled=True but not RUNNING → needs reboot to deploy
            # - enabled=False but RUNNING → needs reboot to stop
            # - DRAFT state (no bootstrap) → no reboot needed, just needs config
            if item.state == "DRAFT":
                item.needs_reboot = False
            elif item.enabled and item.state != "RUNNING":
                item.needs_reboot = True
            elif not item.enabled and item.state == "RUNNING":
                item.needs_reboot = True
            else:
                item.needs_reboot = False

        return Template("expose/main.html", context={"items": items})

    @get(
        "/new",
        summary="Create expose page",
        description="Display form for creating a new expose item.",
        operation_id="expose_new_page",
    )
    async def new_page(self, state: State) -> Template:
        """Render the create expose form."""
        return Template("expose/edit.html", context={
            "item": None,
            "is_new": True,
            "networks": state["settings"].masumi_network_names,
            "app_server": state["settings"].sumi_address,
        })

    @get(
        "/edit/{name:str}",
        summary="Edit expose page",
        description="Display form for editing an expose item.",
        operation_id="expose_edit_page",
    )
    async def edit_page(self, name: str, state: State) -> Template:
        """Render the edit expose form."""
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        item = ExposeResponse.from_db_row(row)
        return Template("expose/edit.html", context={
            "item": item,
            "is_new": False,
            "networks": state["settings"].masumi_network_names,
            "app_server": state["settings"].sumi_address,
        })

    @get(
        "/duplicate/{name:str}",
        summary="Duplicate expose page",
        description="Display form for creating a copy of an existing expose item.",
        operation_id="expose_duplicate_page",
    )
    async def duplicate_page(self, name: str, state: State) -> Template:
        """Render the create expose form pre-filled with data from an existing expose."""
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        item = ExposeResponse.from_db_row(row)
        # Generate a unique name for the copy
        base_name = f"{item.name}-copy"
        copy_name = base_name
        counter = 1
        while await db.get_expose(copy_name):
            counter += 1
            copy_name = f"{base_name}-{counter}"

        # Create a modified copy for the template
        # We need to pass the original item but signal it's a new record
        return Template("expose/edit.html", context={
            "item": item,
            "is_new": True,
            "duplicate_name": copy_name,  # Suggested name for the duplicate
            "networks": state["settings"].masumi_network_names,
            "app_server": state["settings"].sumi_address,
        })

    @get(
        "/globals",
        summary="Global config page",
        description="Display the global serve configuration editor.",
        operation_id="expose_globals_page",
    )
    async def globals_page(self, state: State) -> Template:
        """Render the global config editor."""
        ensure_serve_config()
        config_path = Path(RAY_SERVE_CONFIG)
        config_content = config_path.read_text() if config_path.exists() else ""
        return Template("expose/globals.html", context={
            "config": config_content,
            "config_path": RAY_SERVE_CONFIG
        })

    @post(
        "/globals",
        summary="Save global config",
        description="Save the global serve configuration.",
        operation_id="expose_globals_save",
    )
    async def save_globals(self, request: Request, state: State) -> Template | Redirect:
        """Save global config and redirect."""
        form_data = await request.form()
        config_content = form_data.get("config", "")

        # Validate YAML
        try:
            yaml.safe_load(config_content)
        except yaml.YAMLError as e:
            return Template("expose/globals.html", context={
                "config": config_content,
                "config_path": RAY_SERVE_CONFIG,
                "error": f"Invalid YAML: {e}"
            })

        # Save
        config_path = Path(RAY_SERVE_CONFIG)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config_content)

        return Redirect(path="/admin/expose")


class BootControl(litestar.Controller):
    """Controller for boot/shutdown endpoints."""

    path = "/boot"
    tags = ["Boot"]
    guards = [operator_guard]

    @post(
        "",
        summary="Boot all enabled exposures",
        description="Start Ray Serve deployment for all enabled exposures. Returns streaming text output.",
        operation_id="boot_start",
    )
    async def boot(
        self,
        request: Request,
        state: State,
        force: bool = False,
    ) -> Stream:
        """
        Execute boot process with streaming output.

        The boot runs as a background task so it continues even if
        the client disconnects. The initiator subscribes to the
        message stream just like late joiners.

        Args:
            force: Override existing boot lock if True
        """
        # Get settings
        ray_dashboard = state["settings"].RAY_DASHBOARD
        # Get Ray Serve address from serve config (with fallback to settings)
        ray_serve_address = get_ray_serve_address_from_config(
            fallback=state["settings"].RAY_SERVE_ADDRESS
        )
        app_server = state["settings"].APP_SERVER
        boot_timeout = state["settings"].BOOT_HEALTH_TIMEOUT

        # Get auth cookies from request
        auth_cookies = dict(request.cookies)

        # Get username for audit logging
        owner = await get_username(request.user, state) if request.user else "operator"

        # Start boot as background task
        started = await start_boot_background(
            ray_dashboard=ray_dashboard,
            ray_serve_address=ray_serve_address,
            app_server=app_server,
            auth_cookies=auth_cookies,
            force=force,
            owner=owner,
            boot_timeout=boot_timeout
        )

        if not started and not force:
            # Boot already in progress, return error
            async def already_running():
                yield "[ERROR] Boot already in progress. Use force=true to override.\n"
            return Stream(already_running(), media_type="text/plain")

        # Subscribe to message stream (same as late joiner)
        queue = boot_lock.subscribe()

        async def generate():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                        yield f"{msg}\n"
                        if msg.step in (BootStep.COMPLETE, BootStep.ERROR):
                            break
                    except asyncio.TimeoutError:
                        if not boot_lock.is_locked and queue.empty():
                            break
                        continue
            finally:
                boot_lock.unsubscribe(queue)

        return Stream(generate(), media_type="text/plain")

    @get(
        "",
        summary="Get boot status",
        description="Get current boot status and messages if boot is in progress.",
        operation_id="boot_status",
    )
    async def boot_status(self, state: State) -> dict:
        """Get current boot lock status."""
        return {
            "locked": boot_lock.is_locked,
            "lock_time": boot_lock.lock_time,
            "messages": [str(m) for m in boot_lock.messages]
        }

    @get(
        "/stream",
        summary="Stream boot messages",
        description="Subscribe to boot message stream (for operators joining an in-progress boot).",
        operation_id="boot_stream",
    )
    async def boot_stream(self, state: State) -> Stream:
        """Stream boot messages to client."""
        if not boot_lock.is_locked:
            async def no_boot():
                yield "No boot in progress\n"
            return Stream(no_boot(), media_type="text/plain")

        queue = boot_lock.subscribe()

        async def generate():
            try:
                while True:
                    try:
                        # Short timeout to check for new messages
                        msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                        yield f"{msg}\n"
                        if msg.step in (BootStep.COMPLETE, BootStep.ERROR):
                            break
                    except asyncio.TimeoutError:
                        # If lock released and queue empty, we're done
                        if not boot_lock.is_locked and queue.empty():
                            break
                        continue
            finally:
                boot_lock.unsubscribe(queue)

        return Stream(generate(), media_type="text/plain")

    @delete(
        "",
        summary="Shutdown Ray Serve",
        description="Execute serve shutdown command.",
        operation_id="boot_shutdown",
        status_code=200,
    )
    async def shutdown(self, request: Request, state: State) -> Stream:
        """Execute shutdown with streaming output."""
        # Get app server and auth cookies for flow register call
        app_server = str(request.base_url).rstrip("/")
        auth_cookies = dict(request.cookies) if request.cookies else None

        # Get username for audit logging
        owner = await get_username(request.user, state) if request.user else "operator"

        async def generate():
            async for msg in run_shutdown(app_server, auth_cookies, owner):
                yield f"{msg}\n"

        return Stream(generate(), media_type="text/plain")

    @post(
        "/refresh/{name:str}",
        summary="Refresh single expose",
        description="Refresh a single expose by: disable → boot → enable → boot.",
        operation_id="boot_refresh_expose",
        status_code=200,
    )
    async def refresh_expose(
        self,
        name: str,
        request: Request,
        state: State,
    ) -> Stream:
        """
        Refresh a single expose.

        This runs the full refresh cycle:
        1. Disable the expose
        2. Run boot process (removes the expose's flows)
        3. Enable the expose
        4. Run boot process again (re-adds the expose's flows)
        """
        from kodosumi.service.expose.boot import run_refresh_expose

        # Check if expose exists
        await db.init_database()
        expose = await db.get_expose(name)
        if not expose:
            async def not_found():
                yield f"[ERROR] Expose '{name}' not found\n"
            return Stream(not_found(), media_type="text/plain")

        # Get config from state
        ray_dashboard = state["settings"].RAY_DASHBOARD
        ray_serve_address = get_ray_serve_address_from_config()
        app_server = state["settings"].APP_SERVER
        auth_cookies = dict(request.cookies) if request.cookies else None

        async def generate():
            async for msg in run_refresh_expose(
                expose_name=name,
                ray_dashboard=ray_dashboard,
                ray_serve_address=ray_serve_address,
                app_server=app_server,
                auth_cookies=auth_cookies,
            ):
                yield f"{msg}\n"

        return Stream(generate(), media_type="text/plain")


class BootUIControl(litestar.Controller):
    """Controller for boot UI pages."""

    path = "/admin/expose/boot"
    tags = ["Boot UI"]
    guards = [operator_guard]

    @get(
        "",
        summary="Boot screen",
        description="Display the boot console screen.",
        operation_id="boot_page",
    )
    async def boot_page(self, state: State) -> Template:
        """Render the boot screen."""
        return Template("expose/boot.html", context={
            "is_locked": boot_lock.is_locked,
            "messages": [str(m) for m in boot_lock.messages]
        })

    @get(
        "/shutdown",
        summary="Shutdown confirmation screen",
        description="Display shutdown confirmation dialog.",
        operation_id="shutdown_page",
    )
    async def shutdown_page(self, state: State) -> Template:
        """Render the shutdown confirmation screen."""
        return Template("expose/shutdown.html", context={})

    @get(
        "/refresh/{name:str}",
        summary="Refresh expose screen",
        description="Display boot console for refreshing a single expose.",
        operation_id="refresh_expose_page",
    )
    async def refresh_expose_page(self, name: str, state: State) -> Template:
        """Render the boot console for refreshing a specific expose."""
        return Template("expose/boot.html", context={
            "is_locked": boot_lock.is_locked,
            "messages": [str(m) for m in boot_lock.messages],
            "refresh_expose": name,
        })


class MaintenanceControl(litestar.Controller):
    """
    Controller for maintenance page.

    This is shown to regular users when the system is undergoing
    boot/deployment. No authentication required.
    """

    path = "/maintenance"
    tags = ["Maintenance"]
    # No guards - accessible to everyone

    @get(
        "",
        summary="Maintenance page",
        description="Display maintenance page during system boot.",
        operation_id="maintenance_page",
    )
    async def maintenance_page(self, state: State) -> Template | Redirect:
        """
        Render the maintenance page.

        If not in maintenance mode (boot not in progress), redirect to home.
        """
        if not boot_lock.is_locked:
            # Not in maintenance, redirect to home
            return Redirect(path="/")

        return Template("expose/maintenance.html", context={
            "is_booting": True
        })


class ExchangeControl(litestar.Controller):
    """Controller for export/import endpoints."""

    path = "/exchange"
    tags = ["Exchange"]
    guards = [operator_guard]

    @get(
        "/export",
        summary="Export expose database",
        description="Export all expose items to JSON format.",
        operation_id="exchange_export",
    )
    async def export_exposes(self, state: State) -> dict:
        """Export all expose items to JSON."""
        await db.init_database()
        rows = await db.get_all_exposes()

        # Convert to list of dicts with parsed meta
        items = []
        for row in rows:
            item = dict(row)
            # Parse meta YAML to list for cleaner JSON export
            if item.get("meta"):
                try:
                    item["meta"] = yaml.safe_load(item["meta"])
                except yaml.YAMLError:
                    pass  # Keep as string if parse fails
            items.append(item)

        return {
            "version": "1.0",
            "exported_at": time.time(),
            "count": len(items),
            "exposes": items,
        }

    @post(
        "/import",
        summary="Import expose database",
        description="Import expose items from JSON. Creates backup before import.",
        operation_id="exchange_import",
    )
    async def import_exposes(
        self, request: Request, state: State
    ) -> dict:
        """
        Import expose items from JSON.

        Creates a backup of current database before import.
        """
        from datetime import datetime
        import shutil
        import json

        # Parse JSON body
        try:
            body = await request.body()
            data = json.loads(body)
        except (json.JSONDecodeError, Exception) as e:
            raise ValidationException(detail=f"Invalid JSON: {e}")

        # Validate structure
        if not isinstance(data, dict):
            raise ValidationException(detail="Expected JSON object")

        exposes = data.get("exposes", [])
        if not isinstance(exposes, list):
            raise ValidationException(detail="Expected 'exposes' to be a list")

        # Create backup
        db_path = Path(db.EXPOSE_DATABASE)
        if db_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup_path = db_path.with_suffix(f".{timestamp}.db")
            shutil.copy2(db_path, backup_path)
            backup_created = str(backup_path)
        else:
            backup_created = None

        # Import items
        await db.init_database()
        now = time.time()
        imported = 0
        errors = []

        for item in exposes:
            try:
                name = item.get("name")
                if not name:
                    errors.append("Item missing 'name' field")
                    continue

                # Convert meta back to YAML if it's a list
                meta = item.get("meta")
                if isinstance(meta, list):
                    meta = yaml.dump(meta, default_flow_style=False, allow_unicode=True)

                await db.upsert_expose(
                    name=name,
                    display=item.get("display"),
                    network=item.get("network"),
                    enabled=bool(item.get("enabled", True)),
                    state=item.get("state", "DRAFT"),
                    heartbeat=item.get("heartbeat") or now,
                    bootstrap=item.get("bootstrap"),
                    meta=meta,
                )
                imported += 1
            except Exception as e:
                errors.append(f"{item.get('name', 'unknown')}: {e}")

        return {
            "imported": imported,
            "errors": errors,
            "backup": backup_created,
        }


class ExchangeUIControl(litestar.Controller):
    """Controller for exchange UI page."""

    path = "/admin/expose/exchange"
    tags = ["Exchange UI"]
    guards = [operator_guard]

    @get(
        "",
        summary="Exchange page",
        description="Display import/export page.",
        operation_id="exchange_page",
    )
    async def exchange_page(self, state: State) -> Template:
        """Render the exchange page."""
        return Template("expose/exchange.html", context={})


class AuditLogControl(litestar.Controller):
    """Controller for audit log viewing."""

    path = "/audit"
    tags = ["Audit"]
    guards = [operator_guard]

    @get(
        "/stream",
        summary="Stream audit log",
        description="Stream audit log entries from offset. Only INFO level (no sensitive details).",
        operation_id="audit_stream",
    )
    async def stream_audit_log(
        self,
        state: State,
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        """
        Stream audit log entries from a given byte offset.

        Args:
            offset: Byte offset to start reading from (default: 0)
            limit: Maximum number of lines to return (default: 100)

        Returns:
            dict with:
            - lines: List of log lines (INFO level only)
            - next_offset: Byte offset for next read
            - file_size: Current file size
        """
        audit_log_path = Path(state["settings"].AUDIT_LOG_FILE).resolve()

        if not audit_log_path.exists():
            return {
                "lines": [f"Audit log file not found: {audit_log_path}"],
                "next_offset": 0,
                "file_size": 0,
            }

        file_size = audit_log_path.stat().st_size

        # If offset is beyond file size (e.g., after rotation), reset to 0
        if offset > file_size:
            offset = 0

        lines = []
        next_offset = offset
        try:
            with open(audit_log_path, "r", encoding="utf-8") as f:
                f.seek(offset)
                bytes_read = 0
                max_bytes = 64 * 1024  # 64KB max read per request

                while True:
                    line = f.readline()
                    if not line:
                        break

                    line_bytes = len(line.encode("utf-8"))
                    bytes_read += line_bytes

                    # Filter: only INFO level and above (no DEBUG)
                    # Format: "2024-01-01 00:00:00,000 INFO - message"
                    if " INFO " in line or " WARNING " in line or " ERROR " in line:
                        lines.append(line.rstrip())

                    if len(lines) >= limit or bytes_read >= max_bytes:
                        break

                next_offset = f.tell()

        except Exception as e:
            return {
                "lines": [f"Error reading audit log: {e}"],
                "next_offset": offset,
                "file_size": file_size,
            }

        return {
            "lines": lines,
            "next_offset": next_offset,
            "file_size": file_size,
        }

    @get(
        "/info",
        summary="Audit log info",
        description="Get audit log file information.",
        operation_id="audit_info",
    )
    async def audit_log_info(self, state: State) -> dict:
        """Get audit log file information."""
        audit_log_path = Path(state["settings"].AUDIT_LOG_FILE)

        if not audit_log_path.exists():
            return {
                "exists": False,
                "path": str(audit_log_path),
                "size": 0,
                "max_bytes": state["settings"].AUDIT_LOG_MAX_BYTES,
                "backup_count": state["settings"].AUDIT_LOG_BACKUP_COUNT,
            }

        return {
            "exists": True,
            "path": str(audit_log_path),
            "size": audit_log_path.stat().st_size,
            "max_bytes": state["settings"].AUDIT_LOG_MAX_BYTES,
            "backup_count": state["settings"].AUDIT_LOG_BACKUP_COUNT,
            "modified": audit_log_path.stat().st_mtime,
        }
