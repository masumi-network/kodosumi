"""
Read and write the meta YAML of a single flow inside an expose.

An expose stores a list of flow entries, each holding its own YAML string.
The registry endpoints read the registration keys out of that string and
write them back, so both live here instead of on one controller.
"""

from typing import Optional

import yaml

from kodosumi.service.expose import db
from kodosumi.service.expose.locks import keyed_lock


def _parse_meta_list(row: dict) -> Optional[list]:
    """Parse the entry list of an expose, or None when it is unusable.

    The column holds hand editable YAML, so a scalar or a list of scalars
    reaches this function.  Entries stay in the list even when they are
    not dicts, because the writer dumps this same list back to the column
    and must not delete what it does not understand.  Every loop over the
    result therefore skips non dict entries itself.
    """
    if not row.get("meta"):
        return None
    try:
        meta_list = yaml.safe_load(row["meta"])
    except yaml.YAMLError:
        return None
    if not isinstance(meta_list, list) or not meta_list:
        return None
    return meta_list


def get_flow_meta(row: dict, flow_url: Optional[str]) -> Optional[dict]:
    """Parse meta YAML and return the data dict for a specific flow URL.

    An empty URL matches nothing.  A request that omits flow_url must not
    silently act on the first flow of the expose: the registry endpoints
    burn and mint on chain, so the wrong flow is not recoverable.
    """
    if not flow_url:
        return None
    meta_list = _parse_meta_list(row)
    if meta_list is None:
        return None

    for entry in meta_list:
        if not isinstance(entry, dict):
            continue
        if entry.get("url", "") != flow_url:
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

    The write is serialised per expose and reads the column again inside
    that lock: the caller's row was read earlier, and another request may
    have changed a different flow of the same expose since.
    """
    # One lock per expose. Every flow of an expose shares a single meta
    # column, so two writers that each read it, change their own flow and
    # write the whole column back would drop one of the two changes.
    async with keyed_lock(f"expose\n{expose_name}"):
        return await _apply_flow_meta(
            await _fresh_row(expose_name, row),
            expose_name, flow_url, updates, base_data)


async def _fresh_row(expose_name: str, row: dict) -> dict:
    """Re-read the expose, falling back to the caller's copy."""
    try:
        return await db.get_expose(expose_name) or row
    except Exception:
        return row


async def _apply_flow_meta(
    row: dict, expose_name: str, flow_url: str, updates: dict,
    base_data: Optional[str] = None,
) -> Optional[str]:
    meta_list = _parse_meta_list(row)
    if meta_list is None:
        return None

    updated = False
    updated_data_yaml = None
    for entry in meta_list:
        if not isinstance(entry, dict) or entry.get("url") != flow_url:
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
