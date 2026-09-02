"""
Read and write the meta YAML of a single flow inside an expose.

An expose stores a list of flow entries, each holding its own YAML string.
The registry endpoints read the registration keys out of that string and
write them back, so both live here instead of on one controller.
"""

import math
from typing import Optional

import yaml

from kodosumi.service.expose import db
from kodosumi.service.expose.locks import keyed_lock

REGISTRY_MANAGED_KEYS = {
    "agentIdentifier",
    "registrationId",
    "paymentSourceType",
    "supportedPaymentSourceIndex",
    "pendingMigration",
    "previousRegistration",
    "deregistrationState",
    "migrationError",
}


def registry_action_lock(expose_name: str):
    """Serialize a registry mint with form updates of the same expose."""
    return keyed_lock(f"registry-action\n{expose_name}")


class UpdatedFlowYaml(str):
    """YAML text with the exact expose ETag written beside it."""

    etag: str
    previous_etag: str

    def __new__(
        cls, yaml_text: str, previous_etag: float, etag: float
    ):
        value = super().__new__(cls, yaml_text)
        value.etag = str(etag)
        value.previous_etag = str(previous_etag)
        return value


def flow_meta_update_fields(updated_yaml: Optional[str]) -> dict:
    """Build JSON-safe fields for a metadata update response."""
    return {
        "updatedYaml": (
            str(updated_yaml) if updated_yaml is not None else None),
        "updatedEtag": getattr(updated_yaml, "etag", None),
        "previousEtag": getattr(updated_yaml, "previous_etag", None),
    }


def compose_flow_meta_updates(
    first: Optional[str], last: Optional[str]
) -> Optional[str]:
    """Keep the first prior ETag and the last YAML across two writes."""
    if last is None:
        return first
    if first is None:
        return last
    previous_etag = getattr(first, "previous_etag", None)
    first_etag = getattr(first, "etag", None)
    last_previous_etag = getattr(last, "previous_etag", None)
    updated_etag = getattr(last, "etag", None)
    if previous_etag is None or updated_etag is None:
        return last
    if first_etag != last_previous_etag:
        return last
    return UpdatedFlowYaml(str(last), previous_etag, updated_etag)


def compose_flow_meta_update_fields(first: dict, last: dict) -> dict:
    """Compose JSON update fields produced by consecutive metadata writes."""
    if last.get("updatedYaml") is not None:
        is_contiguous = (
            first.get("updatedEtag") == last.get("previousEtag"))
        return {
            "updatedYaml": last["updatedYaml"],
            "updatedEtag": last.get("updatedEtag"),
            "previousEtag": (
                first.get("previousEtag")
                if first.get("updatedYaml") is not None and is_contiguous
                else last.get("previousEtag")),
        }
    if first.get("updatedYaml") is not None:
        return {
            key: first.get(key)
            for key in ("updatedYaml", "updatedEtag", "previousEtag")
        }
    return flow_meta_update_fields(None)


def parse_flow_etag(value: object) -> Optional[float]:
    """Parse a form ETag before an external registry action."""
    if value is None or value == "":
        return None
    try:
        etag = float(value)
    except (TypeError, ValueError):
        raise ValueError("meta_etag must be a finite number")
    if not math.isfinite(etag):
        raise ValueError("meta_etag must be a finite number")
    return etag


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


def has_registry_lifecycle(row: dict) -> bool:
    """Return whether any flow still owns registry state."""
    meta_list = _parse_meta_list(row) or []
    for entry in meta_list:
        if not isinstance(entry, dict):
            continue
        data = entry.get("data", "")
        try:
            flow = yaml.safe_load(data) if data else {}
        except yaml.YAMLError:
            continue
        if isinstance(flow, dict) and any(
                flow.get(key) is not None for key in REGISTRY_MANAGED_KEYS):
            return True
    return False


async def update_flow_meta(
    row: dict, expose_name: str, flow_url: str, updates: dict,
    base_data: Optional[str] = None,
    base_etag: Optional[float] = None,
    expected: Optional[dict] = None,
    conditional_updates: Optional[dict] = None,
    expected_network: Optional[str] = None,
) -> Optional[UpdatedFlowYaml]:
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
    guarded_network = (
        expected_network if expected_network is not None
        else row.get("network"))
    async with keyed_lock(f"expose\n{expose_name}"):
        current = await _fresh_row(expose_name, row)
        for _ in range(3):
            result = await _apply_flow_meta(
                current,
                expose_name,
                flow_url,
                updates,
                base_data,
                base_etag,
                expected,
                conditional_updates,
                guarded_network,
            )
            if result is not None:
                return result
            latest = await _fresh_row(expose_name, current)
            if (
                latest.get("updated") == current.get("updated")
                and latest.get("meta") == current.get("meta")
            ):
                return None
            current = latest
        return None


async def _fresh_row(expose_name: str, row: dict) -> dict:
    """Re-read the expose, falling back to the caller's copy."""
    try:
        return await db.get_expose(expose_name) or row
    except Exception:
        return row


async def _apply_flow_meta(
    row: dict, expose_name: str, flow_url: str, updates: dict,
    base_data: Optional[str] = None,
    base_etag: Optional[float] = None,
    expected: Optional[dict] = None,
    conditional_updates: Optional[dict] = None,
    expected_network: Optional[str] = None,
) -> Optional[UpdatedFlowYaml]:
    if (expected_network is not None
            and row.get("network") != expected_network):
        return None
    meta_list = _parse_meta_list(row)
    if meta_list is None:
        return None

    updated = False
    updated_data_yaml = None
    for entry in meta_list:
        if not isinstance(entry, dict) or entry.get("url") != flow_url:
            continue

        stored_data_str = entry.get("data", "")
        try:
            stored = yaml.safe_load(stored_data_str) \
                if stored_data_str else {}
            if not isinstance(stored, dict):
                stored = {}
        except yaml.YAMLError:
            stored = {}

        if expected and any(
                stored.get(key) != value for key, value in expected.items()):
            return None

        current_etag = float(row.get("updated") or 0)
        is_current_base = (
            base_etag is not None and base_etag == current_etag)
        use_base_data = base_data is not None and is_current_base
        data_str = base_data if use_base_data else stored_data_str
        try:
            parsed = yaml.safe_load(data_str) if data_str else {}
            if not isinstance(parsed, dict):
                parsed = {}
        except yaml.YAMLError:
            parsed = {}

        if use_base_data:
            for key in REGISTRY_MANAGED_KEYS:
                if key in stored:
                    parsed[key] = stored[key]
                else:
                    parsed.pop(key, None)

        applied_updates = (
            dict(conditional_updates or {}) if is_current_base else {})
        applied_updates.update(updates)
        for key, value in applied_updates.items():
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
        next_etag = db.next_expose_etag(current_etag)
        saved = await db.update_expose_meta(
            expose_name,
            new_meta_yaml,
            updated=next_etag,
            expected_updated=current_etag,
            expected_meta=row.get("meta"),
        )
        if not saved:
            return None
        return UpdatedFlowYaml(updated_data_yaml, current_etag, next_etag)

    return None
