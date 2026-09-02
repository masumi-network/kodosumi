"""Response builders for Masumi registry controller reads."""

from kodosumi.service.expose.registration import rail_fields


def registry_row_response(
    result: dict,
    agent_id: str | None,
    registration_id: str | None,
    meta_data: dict,
    migration: dict | None,
    update_fields: dict,
) -> dict:
    """Build the status response for a registry row returned by Masumi."""
    transaction = result.get("CurrentTransaction") or {}
    error_message = (
        transaction.get("errorMessage") or transaction.get("error") or "")
    top_error = result.get("error") or ""
    if not error_message and top_error != "{}":
        error_message = top_error
    return {
        "registered": result.get("state") == "RegistrationConfirmed",
        "state": result.get("state", "Unknown"),
        "agentIdentifier": result.get("agentIdentifier") or agent_id,
        "registrationId": result.get("id") or registration_id,
        "name": result.get("name"),
        "transaction": transaction or None,
        "errorMessage": error_message,
        "migration": migration,
        **update_fields,
        **rail_fields(meta_data),
    }
