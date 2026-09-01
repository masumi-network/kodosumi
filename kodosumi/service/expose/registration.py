"""
Build the MIP-002 agent fields of a flow out of its meta YAML.

Both the first registration of a flow and a later migration to another
payment rail send the same display fields, so they are read in one place.
"""

from typing import Any, Dict, Optional


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
