"""
Controller for expose API endpoints.

All endpoints require operator role authentication.
"""

import logging
import time
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

import yaml
import litestar
from litestar import Request, delete, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException, NotFoundException
from litestar.response import Redirect, Template

from kodosumi.helper import HTTPXClient
from kodosumi.service.expose.boot import (
    BootMessage,
    boot_lock,
    get_ray_serve_address_from_config,
    run_boot_process,
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

        # Read flows directly from DB (not via HTTP to avoid deadlock
        # with single-worker Uvicorn calling GET /flow on itself).
        from kodosumi.service.flow import _get_all_flows
        all_flow_eps = await _get_all_flows()
        flow_by_path = {}
        for ep in all_flow_eps:
            url_path = get_path_from_base_url(ep.base_url)
            flow_by_path[url_path] = ep.model_dump()

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

        # Read flows directly from DB (not via HTTP to avoid deadlock).
        from kodosumi.service.flow import _get_all_flows
        all_flow_eps = await _get_all_flows()
        flow_by_path = {}
        for ep in all_flow_eps:
            url_path = get_path_from_base_url(ep.base_url)
            flow_by_path[url_path] = ep.model_dump()

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
