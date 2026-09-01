"""
Controller for the exchange endpoints of an expose.

Imports and exports expose definitions as YAML so an operator can move a
setup between installations.

All endpoints require operator role authentication.
"""

import logging
import time
from pathlib import Path

import yaml
import litestar
from litestar import Request, get, post
from litestar.datastructures import State
from litestar.exceptions import ValidationException
from litestar.response import Template

from kodosumi.service.jwt import operator_guard
from kodosumi.service.expose import db

logger = logging.getLogger(__name__)


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
