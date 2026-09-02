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
    migrationError        why the last attempt stopped, cleared on retry

A migration moves in two steps, and each one is written back before the
next begins: confirm the new mint, then burn the replaced agent. The burn
is irreversible, so it never runs as a side effect of a read.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from kodosumi.config import MasumiConfig
from kodosumi.service.expose import db
from kodosumi.service.expose.flow_meta import get_flow_meta, update_flow_meta
from kodosumi.service.expose.locks import keyed_lock
from kodosumi.service.expose.registry import (
    DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX,
    PAYMENT_SOURCE_TYPE_V1,
    PAYMENT_SOURCE_TYPE_V2,
    deregister_agent,
    get_registration_status,
)

logger = logging.getLogger(__name__)

# The one terminal failure the payment node reports for a mint. Every other
# state is still in flight and keeps the migration waiting.
FAILED_REGISTRATION_STATE = "RegistrationFailed"
CONFIRMED_REGISTRATION_STATE = "RegistrationConfirmed"

def migration_lock(expose_name: str, flow_url: str) -> asyncio.Lock:
    """The lock every burn and swap decision of one flow has to hold.

    The admin panel calls in from every open tab, and without this two
    callers read the same pre swap metadata, both write the swap, and both
    burn the same agent. The automatic burn runs on the poll endpoint and
    the manual one on its own endpoint, so they share this lock rather
    than each taking their own.
    """
    return keyed_lock(f"migration\n{expose_name}\n{flow_url}")


def pending_migration(meta_data: dict) -> Optional[Dict[str, Any]]:
    """Return the migration a flow is waiting on, or None."""
    pending = meta_data.get("pendingMigration")
    if isinstance(pending, dict) and pending.get("registrationId"):
        return pending
    return None


def burn_target(meta_data: dict) -> Optional[Dict[str, Any]]:
    """Return the replaced agent this migration still has to burn, or None.

    The intent to burn outlives pendingMigration on purpose: the swap and
    the burn are separate steps and can land in separate requests.
    """
    previous = meta_data.get("previousRegistration")
    if not isinstance(previous, dict):
        return None
    if not previous.get("agentIdentifier"):
        return None
    return previous if previous.get("deregisterRequested") else None


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
        # A new attempt starts clean: the error of the last one is history.
        "migrationError": None,
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
        "deregisterRequested": bool(pending.get("deregisterPrevious")),
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


def failed_migration_updates(
    state: str, error: Optional[str] = None
) -> Dict[str, Any]:
    """Meta keys written when the V2 mint failed for good.

    Clearing pendingMigration is what lets the operator try again. While it
    stands, the migrate endpoint answers 409 "already waiting", the plain
    deregister endpoint answers 409 too, and the panel hides both buttons,
    so a failed mint would leave the flow with no way forward.
    """
    return {
        "pendingMigration": None,
        "migrationError": error or (
            f"The Web3CardanoV2 registration ended in {state}. "
            "Check the selling wallet balance and start the migration again."
        ),
    }


# Shown once when the operator cancels. It is not written to the flow
# metadata: migrationError drives a standing error in the panel, and a
# deliberate cancel is not a failure to keep reporting.
CANCEL_NOTICE = (
    "The migration was cancelled. If the Web3CardanoV2 agent still confirms "
    "on chain, deregister it in the Masumi admin interface."
)


def cancel_migration_updates() -> Dict[str, Any]:
    """Meta keys written when the operator gives up on a pending mint."""
    return {
        "pendingMigration": None,
        "migrationError": None,
    }


async def advance_migration(
    masumi: MasumiConfig,
    row: dict,
    expose_name: str,
    flow_url: str,
    meta_data: dict,
    allow_burn: bool = False,
) -> Optional[Dict[str, Any]]:
    """Move a migration one step and write the result back.

    Returns None when the flow has nothing to advance. Otherwise returns
    the migration state for the admin panel. Safe to call repeatedly and
    from several requests at once.

    allow_burn stays False on read only paths. Burning the replaced agent
    is irreversible and must never be a side effect of opening a page.
    """
    if not flow_url:
        # Without the url nothing can be written back, and work that is not
        # recorded would repeat on every call.
        return None
    if not pending_migration(meta_data) and not burn_target(meta_data):
        return None

    async with migration_lock(expose_name, flow_url):
        # Re-read inside the lock: another request may have finished the
        # swap while this one waited for it.
        row = await db.get_expose(expose_name) or row
        meta_data = get_flow_meta(row, flow_url) or meta_data

        report: Dict[str, Any] = {}
        pending = pending_migration(meta_data)
        if pending:
            report = await _confirm_mint(
                masumi, row, expose_name, flow_url, meta_data, pending)
            if not report.get("updatedYaml"):
                return report
            row = await db.get_expose(expose_name) or row
            meta_data = get_flow_meta(row, flow_url) or meta_data

        if allow_burn and burn_target(meta_data):
            burn = await _burn_previous(
                masumi, row, expose_name, flow_url, meta_data)
            # A burn on its own still ends a migration, and the panel stops
            # polling on the state rather than on the keys below it.
            report = {"migrationState": "MigrationConfirmed",
                      **report, **burn}

        return report or None


async def _confirm_mint(
    masumi: MasumiConfig,
    row: dict,
    expose_name: str,
    flow_url: str,
    meta_data: dict,
    pending: dict,
) -> Dict[str, Any]:
    """Check the pending V2 mint and swap the flow over once it confirms."""
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

    if state == FAILED_REGISTRATION_STATE:
        updates = failed_migration_updates(state)
        updated_yaml = await update_flow_meta(
            row, expose_name, flow_url, updates)
        logger.warning(
            "Migration of %s%s failed on chain, cleared the pending record",
            expose_name, flow_url)
        return {
            "migrationState": state,
            "migrationError": updates["migrationError"],
            "updatedYaml": updated_yaml,
        }

    if state != CONFIRMED_REGISTRATION_STATE or not new_agent_id:
        return {"migrationState": state}

    # Record the swap before burning anything. A burn that is not written
    # back would repeat, and the old agent stays reachable for a manual
    # burn if this process stops right here.
    updated_yaml = await update_flow_meta(
        row, expose_name, flow_url,
        confirmed_migration_updates(
            meta_data, pending, new_agent_id, keep_previous=True),
    )
    return {
        "migrationState": "MigrationConfirmed",
        "agentIdentifier": new_agent_id,
        "paymentSourceType": pending.get(
            "paymentSourceType", PAYMENT_SOURCE_TYPE_V2),
        "updatedYaml": updated_yaml,
    }


async def _burn_previous(
    masumi: MasumiConfig,
    row: dict,
    expose_name: str,
    flow_url: str,
    meta_data: dict,
) -> Dict[str, Any]:
    """Deregister the agent the migration replaced."""
    previous = burn_target(meta_data) or {}
    old_agent_id = previous.get("agentIdentifier", "")
    try:
        await deregister_agent(masumi, old_agent_id)
    except Exception as e:
        # Non fatal: the V2 agent is live either way. Drop the automatic
        # intent so a node that keeps refusing is not called on every poll,
        # and record why, because the panel forgets between page loads.
        logger.warning(
            "Migration could not deregister %s: %s", old_agent_id, e)
        updated_yaml = await update_flow_meta(
            row, expose_name, flow_url,
            {
                "previousRegistration": {
                    **previous, "deregisterRequested": False},
                "migrationError":
                    f"Could not deregister the replaced agent "
                    f"{old_agent_id}: {e}",
            },
        )
        return {"deregisterError": str(e), "updatedYaml": updated_yaml}

    updated_yaml = await update_flow_meta(
        row, expose_name, flow_url,
        {"previousRegistration": None, "migrationError": None},
    )
    return {
        "deregisteredPrevious": old_agent_id,
        "updatedYaml": updated_yaml,
    }
