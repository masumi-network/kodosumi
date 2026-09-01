"""
Read and write the meta YAML of a single flow inside an expose.

An expose stores a list of flow entries, each holding its own YAML string.
The registry endpoints read the registration keys out of that string and
write them back, so both live here instead of on one controller.
"""

from typing import Optional

import yaml

from kodosumi.service.expose import db


def get_flow_meta(row: dict, flow_url: Optional[str]) -> Optional[dict]:
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

async def update_flow_meta(
row: dict, expose_name: str, flow_url: str, updates: dict,
base_data: Optional[str] = None,
) -> Optional[str]:
    """Update fields in a flow's meta YAML data and save to DB.

    If base_data is provided, it replaces the stored data YAML for
    this flow before applying updates.  This allows the caller to
    pass the live textarea content so unsaved edits are preserved.

    Returns the updated data YAML string for the flow, or None.
    """
    if not row.get("meta"):
        return None

    try:
        meta_list = yaml.safe_load(row["meta"])
        if not meta_list:
            return None
    except yaml.YAMLError:
        return None

    updated = False
    updated_data_yaml = None
    for entry in meta_list:
        if entry.get("url") != flow_url:
            continue

        data_str = base_data if base_data else entry.get("data", "")
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

        updated_data_yaml = yaml.dump(
            parsed,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        entry["data"] = updated_data_yaml
        updated = True
        break

    if updated:
        new_meta_yaml = yaml.dump(
            meta_list,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        await db.update_expose_meta(expose_name, new_meta_yaml)

    return updated_data_yaml
