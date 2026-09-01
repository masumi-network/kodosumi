"""
Move a registered flow from one payment rail to the next.

Masumi cannot upgrade a registration in place: the V1 mint contract has no
update action, so a migration mints a second agent under the V2 policy and
burns the first one only afterwards. The flow keeps serving its V1 agent
identifier while the new mint waits for confirmation, so a migration costs
no downtime.

The meta YAML of the flow carries the whole state:

    pendingMigration      the V2 registration that is still minting
    previousRegistration  the V1 agent that is still on chain after the swap
"""

import logging
from typing import Any, Dict, Optional

from kodosumi.config import MasumiConfig
from kodosumi.service.expose.flow_meta import update_flow_meta
from kodosumi.service.expose.registry import (
    DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX,
    PAYMENT_SOURCE_TYPE_V1,
    PAYMENT_SOURCE_TYPE_V2,
    deregister_agent,
    get_registration_status,
)

logger = logging.getLogger(__name__)


def pending_migration(meta_data: dict) -> Optional[Dict[str, Any]]:
    """Return the migration a flow is waiting on, or None."""
    pending = meta_data.get("pendingMigration")
    if isinstance(pending, dict) and pending.get("registrationId"):
        return pending
    return None


def start_migration_updates(
    registration_id: str, deregister_previous: bool
) -> Dict[str, Any]:
    """Meta keys written when the V2 mint is requested.

    agentIdentifier and registrationId stay untouched on purpose: the V1
    agent keeps answering jobs until the new mint confirms.
    """
    return {
        "pendingMigration": {
            "registrationId": registration_id,
            "paymentSourceType": PAYMENT_SOURCE_TYPE_V2,
            "supportedPaymentSourceIndex":
                DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX,
            "deregisterPrevious": bool(deregister_previous),
        },
    }


def confirmed_migration_updates(
    meta_data: dict, pending: dict, new_agent_id: str, keep_previous: bool
) -> Dict[str, Any]:
    """Meta keys written when the V2 mint is confirmed.

    The flow switches to the new agent identifier and rail. The old agent
    is recorded so the admin panel can still burn it, unless it was burned
    as part of this migration.
    """
    previous = {
        "agentIdentifier": meta_data.get("agentIdentifier"),
        "registrationId": meta_data.get("registrationId"),
        "paymentSourceType": (
            meta_data.get("paymentSourceType") or PAYMENT_SOURCE_TYPE_V1),
    }
    return {
        "agentIdentifier": new_agent_id,
        "registrationId": pending.get("registrationId"),
        "paymentSourceType": pending.get(
            "paymentSourceType", PAYMENT_SOURCE_TYPE_V2),
        "supportedPaymentSourceIndex": pending.get(
            "supportedPaymentSourceIndex",
            DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX),
        "pendingMigration": None,
        "previousRegistration": previous if keep_previous else None,
    }


async def advance_migration(
    masumi: MasumiConfig,
    row: dict,
    expose_name: str,
    flow_url: str,
    meta_data: dict,
) -> Optional[Dict[str, Any]]:
    """Check the pending V2 mint and swap the flow over once it confirms.

    Returns None when the flow has no pending migration. Otherwise returns
    the migration state for the admin panel. Safe to call repeatedly: after
    the swap the flow no longer has a pending migration.
    """
    pending = pending_migration(meta_data)
    if not pending:
        return None

    try:
        result = await get_registration_status(
            masumi,
            registration_id=pending["registrationId"],
            payment_source_type=pending.get(
                "paymentSourceType", PAYMENT_SOURCE_TYPE_V2),
        )
    except Exception as e:
        logger.warning(
            "Migration status error for %s%s: %s", expose_name, flow_url, e)
        return {"migrationState": "Polling", "migrationError": str(e)}

    if not result:
        return {"migrationState": "Polling"}

    state = result.get("state", "Unknown")
    new_agent_id = result.get("agentIdentifier")
    if state != "RegistrationConfirmed" or not new_agent_id:
        return {"migrationState": state}

    # The new agent exists on chain. Burn the old one first when the
    # operator asked for it, so the flow is never listed twice by accident.
    deregister_error = None
    keep_previous = True
    old_agent_id = meta_data.get("agentIdentifier")
    if pending.get("deregisterPrevious") and old_agent_id:
        try:
            await deregister_agent(masumi, old_agent_id)
            keep_previous = False
        except Exception as e:
            # Non fatal: the V2 agent is live either way, and the panel
            # offers the old entry for a manual burn.
            deregister_error = str(e)
            logger.warning(
                "Migration could not deregister %s: %s", old_agent_id, e)

    updated_yaml = await update_flow_meta(
        row, expose_name, flow_url,
        confirmed_migration_updates(
            meta_data, pending, new_agent_id, keep_previous),
    )
    return {
        "migrationState": "MigrationConfirmed",
        "agentIdentifier": new_agent_id,
        "paymentSourceType": pending.get(
            "paymentSourceType", PAYMENT_SOURCE_TYPE_V2),
        "deregisterError": deregister_error,
        "updatedYaml": updated_yaml,
    }
