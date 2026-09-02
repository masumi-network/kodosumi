"""Shared state handling for active and replaced agent deregistration."""

from typing import Optional

from kodosumi.service.expose import db
from kodosumi.service.expose.flow_meta import (flow_meta_update_fields,
                                               get_flow_meta, update_flow_meta)
from kodosumi.service.expose.registration import rail_fields

DEREGISTRATION_INTENT_STATE = "DeregistrationIntent"
DEREGISTRATION_REQUESTED_STATE = "DeregistrationRequested"
DEREGISTRATION_INITIATED_STATE = "DeregistrationInitiated"
DEREGISTRATION_CONFIRMED_STATE = "DeregistrationConfirmed"
DEREGISTRATION_FAILED_STATE = "DeregistrationFailed"
DEREGISTRATION_PENDING_STATES = {
    DEREGISTRATION_REQUESTED_STATE,
    DEREGISTRATION_INITIATED_STATE,
}
ACTIVE_DEREGISTRATION_STATES = {
    DEREGISTRATION_INTENT_STATE,
    *DEREGISTRATION_PENDING_STATES,
    DEREGISTRATION_CONFIRMED_STATE,
    DEREGISTRATION_FAILED_STATE,
}
DEREGISTRATION_READY_STATES = {
    "RegistrationConfirmed",
    "UpdateConfirmed",
    "UpdateFailed",
    DEREGISTRATION_FAILED_STATE,
}


def active_deregistration_updates(state: str) -> dict:
    """Keep the active identity until its burn confirms on chain."""
    updates = {"deregistrationState": state}
    if state == DEREGISTRATION_CONFIRMED_STATE:
        updates.update({
            "agentIdentifier": None,
            "registrationId": None,
            "paymentSourceType": None,
            "supportedPaymentSourceIndex": None,
            "deregistrationState": None,
            "migrationError": None,
        })
    return updates


async def active_deregistration_response(
    row: dict,
    name: str,
    flow_url: str,
    meta_data: dict,
    result: Optional[dict],
    migration: Optional[dict] = None,
) -> Optional[dict]:
    """Persist one active deregistration state and build its response."""
    saved_state = meta_data.get("deregistrationState")
    if saved_state not in ACTIVE_DEREGISTRATION_STATES:
        return None

    remote_state = result.get("state") if result else None
    state = (
        remote_state
        if remote_state in ACTIVE_DEREGISTRATION_STATES
        else saved_state
    )
    updated_yaml = None
    error_message = ""
    if state != saved_state or state == DEREGISTRATION_CONFIRMED_STATE:
        updated_yaml = await update_flow_meta(
            row,
            name,
            flow_url,
            active_deregistration_updates(state),
            expected={
                "agentIdentifier": meta_data.get("agentIdentifier"),
                "registrationId": meta_data.get("registrationId"),
                "deregistrationState": saved_state,
            },
        )
        if updated_yaml is None:
            state = saved_state
            error_message = "The deregistration state could not be saved."

    transaction = (result or {}).get("CurrentTransaction") or {}
    if not error_message:
        error_message = (
            transaction.get("errorMessage")
            or transaction.get("error")
            or (result or {}).get("error")
            or ""
        )
    response_state = (
        DEREGISTRATION_REQUESTED_STATE
        if state == DEREGISTRATION_INTENT_STATE else state
    )
    is_confirmed = response_state == DEREGISTRATION_CONFIRMED_STATE
    response_meta = {} if is_confirmed else meta_data
    return {
        "registered": not is_confirmed,
        "state": response_state,
        "agentIdentifier": (
            None if is_confirmed else meta_data.get("agentIdentifier")),
        "registrationId": (
            None if is_confirmed else meta_data.get("registrationId")),
        "errorMessage": error_message,
        "transaction": transaction or None,
        "migration": migration,
        **flow_meta_update_fields(updated_yaml),
        **rail_fields(response_meta),
    }


async def resume_active_deregistration(
    masumi,
    row: dict,
    name: str,
    flow_url: str,
    meta_data: dict,
    result: Optional[dict],
    lock,
    expected_network: Optional[str] = None,
) -> tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """Submit a saved intent after an interrupted deregistration request."""
    if meta_data.get("deregistrationState") != DEREGISTRATION_INTENT_STATE:
        return row, meta_data, result

    from kodosumi.service.expose.registry import (deregister_agent,
                                                  get_registration_status)

    async with lock:
        current_row = await db.get_expose(name)
        if not current_row:
            return None, None, None
        if (expected_network is not None
                and current_row.get("network") != expected_network):
            return current_row, get_flow_meta(current_row, flow_url), None
        current_meta = get_flow_meta(current_row, flow_url)
        if current_meta is None:
            return current_row, None, None
        if (current_meta.get("deregistrationState")
                != DEREGISTRATION_INTENT_STATE):
            return current_row, current_meta, result

        async def get_current_status():
            return await get_registration_status(
                masumi,
                registration_id=current_meta.get("registrationId"),
                agent_identifier=current_meta.get("agentIdentifier"),
                payment_source_type=current_meta.get("paymentSourceType"),
                registry_row_only=True,
            )

        try:
            current_result = await get_current_status()
        except Exception:
            return current_row, current_meta, result
        if (not current_result
                or current_result.get("state") not in DEREGISTRATION_READY_STATES):
            return current_row, current_meta, current_result

        try:
            current_result = await deregister_agent(
                masumi, current_meta.get("agentIdentifier"))
        except Exception as error:
            try:
                recovered = await get_current_status()
            except Exception:
                recovered = None
            if (recovered
                    and recovered.get("state") in ACTIVE_DEREGISTRATION_STATES):
                current_result = recovered
            elif (recovered
                  and recovered.get("state") in DEREGISTRATION_READY_STATES):
                current_result = {
                    "state": DEREGISTRATION_FAILED_STATE,
                    "error": str(error),
                }
            else:
                current_result = result

        return current_row, current_meta, current_result
