"""
Build the MIP-002 agent fields of a flow out of its meta YAML.

Both the first registration of a flow and a later migration to another
payment rail send the same display fields, so they are read in one place.
"""

from typing import Any, Dict, Optional

import yaml
from litestar.exceptions import ClientException

from kodosumi.service.expose.registry import PAYMENT_SOURCE_TYPE_V1


def build_agent_fields(meta_data: dict, fallback_name: str) -> Dict[str, Any]:
    """Read display, description, tags, author, capability and legal.

    Args:
        meta_data: the parsed data YAML of one flow
        fallback_name: used as the agent name when the flow sets no display

    Returns:
        A dict with the keys the registry client expects. author and
        capability are None when the YAML does not describe them.
    """
    author_data = meta_data.get("author")
    capability_data = meta_data.get("capability")

    author: Optional[Dict[str, str]] = None
    if author_data and isinstance(author_data, dict):
        author = {
            "name": author_data.get("name") or "",
            "contactEmail": author_data.get("contact_email") or "",
            "organization": author_data.get("organization") or "",
        }

    capability: Optional[Dict[str, str]] = None
    if capability_data and isinstance(capability_data, dict):
        capability = {
            "name": capability_data.get("name") or "",
            "version": str(capability_data.get("version", "1.0")),
        }

    tags = meta_data.get("tags", [])
    if not isinstance(tags, list):
        tags = []

    return {
        "name": meta_data.get("display", fallback_name),
        "description": meta_data.get("description", ""),
        "tags": tags,
        "author": author,
        "capability": capability,
        "legal": meta_data.get("legal"),
    }


def sumi_api_base_url(sumi_address: str, flow_url: str) -> str:
    """Build the public MIP-003 base url a buyer calls for this flow."""
    return f"{sumi_address.rstrip('/')}/sumi{flow_url}"


def rail_fields(meta_data: dict) -> Dict[str, Any]:
    """Return the payment rail of a registered flow for the admin UI.

    A flow registered before V2 existed carries no marker, and a V1
    registration never writes one, so an absent value means V1.
    """
    return {
        "paymentSourceType": (
            meta_data.get("paymentSourceType") or PAYMENT_SOURCE_TYPE_V1),
        "supportedPaymentSourceIndex": meta_data.get(
            "supportedPaymentSourceIndex"),
        "previousRegistration": meta_data.get("previousRegistration"),
        # Why the last migration attempt stopped. It lives in the metadata
        # so the reason survives the page load that follows the failure.
        "migrationError": meta_data.get("migrationError"),
        "pendingMigration": meta_data.get("pendingMigration"),
    }


def parse_live_yaml(frontend_yaml: str) -> dict:
    """Parse the YAML the operator sees, so unsaved edits count too."""
    try:
        parsed = yaml.safe_load(frontend_yaml)
    except yaml.YAMLError as e:
        raise ClientException(
            detail=f"YAML parse error in flow metadata: {e}", status_code=422)
    if not isinstance(parsed, dict):
        raise ClientException(
            detail="Invalid YAML format. Expected a mapping (key: value pairs).",
            status_code=422,
        )
    return parsed
