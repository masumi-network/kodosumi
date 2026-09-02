import logging
from contextlib import AsyncExitStack
from typing import Optional

import litestar
from litestar import get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException, NotFoundException

from kodosumi.service.expose import db
from kodosumi.service.expose.deregistration import (
    ACTIVE_DEREGISTRATION_STATES, DEREGISTRATION_FAILED_STATE,
    DEREGISTRATION_INITIATED_STATE, DEREGISTRATION_INTENT_STATE,
    DEREGISTRATION_REQUESTED_STATE, active_deregistration_response,
    active_deregistration_updates, resume_active_deregistration)
from kodosumi.service.expose.flow_meta import (
    compose_flow_meta_update_fields, compose_flow_meta_updates,
    flow_meta_update_fields, get_flow_meta, parse_flow_etag,
    registry_action_lock, update_flow_meta)
from kodosumi.service.expose.migration import advance_migration, migration_lock
from kodosumi.service.expose.registration import (build_agent_fields,
                                                  parse_live_yaml,
                                                  rail_fields,
                                                  sumi_api_base_url)
from kodosumi.service.expose.registry_response import registry_row_response
from kodosumi.service.expose.wallet_control import WalletsControl
from kodosumi.service.jwt import operator_guard

logger = logging.getLogger(__name__)


class RegistryControl(litestar.Controller):
    path = "/expose/{name:str}/registry"
    tags = ["Registry"]
    guards = [operator_guard]

    @get(
        "",
        summary="Get registry status for a flow",
        description="Check Masumi on-chain registry status for a specific flow. Returns registration state, agentIdentifier, and transaction details.",
        operation_id="registry_status",
    )
    async def get_status(
        self, name: str, state: State, flow_url: Optional[str] = None
    ) -> dict:
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            return {"registered": False, "error": "No network configured"}

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            return {"registered": False, "error": str(e)}

        meta_data = get_flow_meta(row, flow_url)
        if meta_data is None:
            return {"registered": False, "error": "Flow not found"}

        agent_id = meta_data.get("agentIdentifier")
        reg_id = meta_data.get("registrationId")

        if not agent_id and not reg_id:
            return {"registered": False, "state": "NotRegistered"}

        # A migration mints a second agent while the first one keeps
        # serving. Finish the swap here so the panel reports the rail the
        # flow actually runs on. allow_burn stays off: this is a GET, and
        # deregistering the replaced agent cannot be undone.
        migration = await advance_migration(
            masumi, row, name, flow_url, meta_data, allow_burn=False,
            expected_network=network)
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")
        meta_data = get_flow_meta(row, flow_url)
        if meta_data is None:
            return {"registered": False, "error": "Flow not found"}
        if row.get("network") != network:
            return {"registered": False, "error": "Network changed. Retry."}
        agent_id = meta_data.get("agentIdentifier")
        reg_id = meta_data.get("registrationId")

        from kodosumi.service.expose.registry import get_registration_status
        try:
            result = await get_registration_status(
                masumi,
                registration_id=reg_id,
                agent_identifier=agent_id,
                payment_source_type=meta_data.get("paymentSourceType"),
                registry_row_only=bool(
                    meta_data.get("deregistrationState")),
            )
        except Exception as e:
            logger.warning("Registry API error for %s: %s", name, e)
            result = None

        active_deregistration = await active_deregistration_response(
            row, name, flow_url, meta_data, result, migration)
        if active_deregistration is not None:
            return active_deregistration

        if not result:
            if agent_id:
                # Trust YAML — agent was confirmed on-chain
                return {
                    "registered": True,
                    "state": "RegistrationConfirmed",
                    "agentIdentifier": agent_id,
                    "registrationId": reg_id,
                    "migration": migration,
                    "pendingMigration": meta_data.get("pendingMigration"),
                    "previousRegistration": meta_data.get(
                        "previousRegistration"),
                    **rail_fields(meta_data),
                }
            return {
                "registered": False,
                "state": "Polling",
                "registrationId": reg_id,
                "migration": migration,
                "pendingMigration": meta_data.get("pendingMigration"),
                "previousRegistration": meta_data.get(
                    "previousRegistration"),
                **rail_fields(meta_data),
            }

        result_reg_id = result.get("id")
        backfilled_yaml = None
        if agent_id and not reg_id and result_reg_id and flow_url:
            backfilled_yaml = await update_flow_meta(row, name, flow_url, {
                "registrationId": result_reg_id,
            }, expected={
                "agentIdentifier": agent_id,
                "registrationId": None,
            })
        update_fields = compose_flow_meta_update_fields(
            migration or {}, flow_meta_update_fields(backfilled_yaml))

        return registry_row_response(
            result, agent_id, reg_id, meta_data, migration, update_fields)

    @post(
        "",
        summary="Register agent on Masumi",
        description="Register an agent flow on the Masumi on-chain registry. Reads display, description, tags, and pricing from the flow's meta YAML. Requires wallet_vkey and flow_url in request body.",
        operation_id="registry_register",
    )
    async def register(
        self, name: str, data: dict, state: State
    ) -> dict:
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            raise ClientException(
                detail="No network configured for this expose", status_code=422)

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)

        from kodosumi.service.expose.registry import (
            DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX, PAYMENT_SOURCE_TYPE_V1,
            PAYMENT_SOURCE_TYPE_V2, get_registration_status, list_wallets,
            pricing_to_yaml_format, pricing_yaml_to_registry, register_agent,
            registry_pricing_to_supported_sources, select_wallet)

        try:
            wallets = await list_wallets(masumi, require_complete=True)
        except Exception as e:
            raise ClientException(
                detail=f"Cannot reach Masumi API: {e}. Check KODO_MASUMI configuration.",
                status_code=502,
            )

        if not wallets:
            raise ClientException(
                detail=f"No wallets found for network '{network}'. "
                       "Check KODO_MASUMI token and payment source configuration.",
                status_code=422,
            )

        flow_url = data.get("flow_url", "")
        wallet_vkey = data.get("wallet_vkey", "")

        if not flow_url:
            raise ClientException(
                detail="flow_url is required", status_code=422)
        if not wallet_vkey:
            raise ClientException(
                detail="wallet_vkey is required", status_code=422)
        try:
            base_etag = parse_flow_etag(data.get("meta_etag"))
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)
        action_etag = (
            base_etag if base_etag is not None
            else float(row.get("updated") or 0))

        try:
            selected_wallet = select_wallet(wallets, wallet_vkey)
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)
        if selected_wallet is None:
            valid_vkeys = [w["walletVkey"] for w in wallets]
            raise ClientException(
                detail=f"Wallet '{wallet_vkey[:8]}...' not found. Available: {[v[:8] + '...' for v in valid_vkeys]}",
                status_code=422,
            )
        payment_source_type = (
            selected_wallet.get("paymentSourceType") or PAYMENT_SOURCE_TYPE_V1)
        is_v2 = payment_source_type == PAYMENT_SOURCE_TYPE_V2

        frontend_yaml = data.get("meta_yaml", "")
        if frontend_yaml and base_etag is None:
            raise ClientException(
                detail="meta_etag is required with meta_yaml",
                status_code=422,
            )
        if frontend_yaml:
            meta_data = parse_live_yaml(frontend_yaml)
        else:
            meta_data = get_flow_meta(row, flow_url)
        if meta_data is None:
            raise ClientException(
                detail=f"Flow '{flow_url}' not found", status_code=404)

        if meta_data.get("agentIdentifier"):
            raise ClientException(
                detail="This flow is already registered. Deregister first to re-register.",
                status_code=409,
            )

        fields = build_agent_fields(meta_data, name)

        pricing_type = data.get("pricing_type")
        amount = data.get("amount")
        currency = data.get("currency")

        reg_network = masumi.registry_network  # "Preprod" or "Mainnet"

        # agentPricing is hand edited, so a malformed shape is an operator
        # mistake that has to name the field, not a 500.
        if pricing_type and pricing_type != "Free" and amount is not None and currency:
            # Use values from dialog
            try:
                yaml_pricing = pricing_to_yaml_format(
                    pricing_type, float(amount), currency, reg_network)
            except (TypeError, ValueError):
                raise ClientException(
                    detail=f"Pricing amount must be a number, got '{amount}'.",
                    status_code=422,
                )
            registry_pricing = pricing_yaml_to_registry(
                yaml_pricing, reg_network)
        elif meta_data.get("agentPricing"):
            # Use values from YAML
            yaml_pricing = meta_data["agentPricing"]
            try:
                registry_pricing = pricing_yaml_to_registry(
                    yaml_pricing, reg_network)
            except ValueError as e:
                raise ClientException(detail=str(e), status_code=422)
        else:
            raise ClientException(
                detail="No pricing configured. Set pricing_type/amount/currency or add agentPricing to the YAML.",
                status_code=422,
            )

        # Compute apiBaseUrl
        api_base_url = sumi_api_base_url(
            state["settings"].sumi_address, flow_url)

        # V2 prices each advertised payment source on its own and rejects the
        # top-level AgentPricing field.
        supported_payment_sources = None
        if is_v2:
            try:
                supported_payment_sources = registry_pricing_to_supported_sources(
                    registry_pricing,
                    reg_network,
                    selected_wallet.get("smartContractAddress", ""),
                )
            except ValueError as e:
                raise ClientException(detail=str(e), status_code=422)

        async with AsyncExitStack() as action:
            await action.enter_async_context(registry_action_lock(name))
            await action.enter_async_context(migration_lock(name, flow_url))
            row = await db.get_expose(name)
            if not row:
                raise NotFoundException(detail=f"Expose '{name}' not found")
            if float(row.get("updated") or 0) != action_etag:
                raise ClientException(
                    detail="The flow changed. Reload before registering.",
                    status_code=409,
                )
            saved_meta = get_flow_meta(row, flow_url)
            if saved_meta is None:
                raise ClientException(
                    detail=f"Flow '{flow_url}' not found", status_code=404)
            if (saved_meta.get("agentIdentifier")
                    or saved_meta.get("pendingMigration")):
                raise ClientException(
                    detail="This flow already has an active or pending "
                           "registration.",
                    status_code=409,
                )
            saved_registration_id = saved_meta.get("registrationId")
            if saved_registration_id:
                previous_result = await get_registration_status(
                    masumi,
                    registration_id=saved_registration_id,
                    payment_source_type=saved_meta.get("paymentSourceType"),
                    registry_row_only=True,
                )
                if (not previous_result
                        or previous_result.get("state")
                        != "RegistrationFailed"):
                    raise ClientException(
                        detail="This flow already has a pending registration.",
                        status_code=409,
                    )
            try:
                result = await register_agent(
                    masumi=masumi,
                    name=fields["name"],
                    description=fields["description"],
                    api_base_url=api_base_url,
                    tags=fields["tags"],
                    pricing=None if is_v2 else registry_pricing,
                    author=fields["author"],
                    capability=fields["capability"],
                    legal=fields["legal"],
                    wallet_vkey=wallet_vkey,
                    supported_payment_sources=supported_payment_sources,
                )
            except RuntimeError as e:
                raise ClientException(detail=str(e), status_code=502)

            registration_id = result.get("id")
            if not registration_id:
                raise ClientException(
                    detail="Masumi accepted the registration but returned "
                           "no registration id. Check Masumi before retrying.",
                    status_code=502,
                )
            meta_updates = {
                "registrationId": registration_id,
                "paymentSourceType": payment_source_type if is_v2 else None,
                "supportedPaymentSourceIndex":
                    DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX if is_v2 else None,
                # The price the agent was minted with. It is saved even
                # when the expose ETag moved during the mint, because the
                # on-chain agent charges this price from now on.
                "agentPricing": (
                    yaml_pricing if pricing_type
                    else meta_data.get("agentPricing")),
            }
            updated_yaml = await update_flow_meta(
                row, name, flow_url, meta_updates,
                base_data=frontend_yaml or None,
                base_etag=action_etag,
                expected={
                    "agentIdentifier": saved_meta.get("agentIdentifier"),
                    "registrationId": saved_registration_id,
                    "pendingMigration": saved_meta.get("pendingMigration"),
                },
            )
            if updated_yaml is None:
                raise ClientException(
                    detail=f"Registration {registration_id} was submitted, "
                           "but its state could not be saved. Do not retry.",
                    status_code=500,
                )

        return {
            "success": True,
            "registrationId": registration_id,
            "state": result.get("state", "RegistrationRequested"),
            "agentIdentifier": result.get("agentIdentifier"),
            "paymentSourceType": payment_source_type,
            **flow_meta_update_fields(updated_yaml),
        }

    @post(
        "/poll",
        summary="Poll registry status and update YAML",
        description="Poll Masumi registry for registration confirmation. Updates meta YAML with agentIdentifier when confirmed. Called periodically by frontend after registration.",
        operation_id="registry_poll",
    )
    async def poll(
        self, name: str, data: dict, state: State
    ) -> dict:
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            return {"error": "No network configured"}

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            return {"error": str(e)}

        flow_url = data.get("flow_url", "")
        meta_data = get_flow_meta(row, flow_url)
        if meta_data is None:
            return {"error": "Flow not found"}

        agent_id = meta_data.get("agentIdentifier")
        reg_id = meta_data.get("registrationId")

        if not reg_id and not agent_id:
            return {"state": "NotRegistered"}

        migration = await advance_migration(
            masumi, row, name, flow_url, meta_data, allow_burn=True,
            expected_network=network)
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")
        meta_data = get_flow_meta(row, flow_url)
        if meta_data is None:
            return {"error": "Flow not found"}
        if row.get("network") != network:
            return {"error": "Network changed. Retry."}
        agent_id = meta_data.get("agentIdentifier")
        reg_id = meta_data.get("registrationId")

        if agent_id and not meta_data.get("deregistrationState"):
            return {
                "state": "RegistrationConfirmed",
                "agentIdentifier": agent_id,
                "migration": migration,
                "updatedYaml": (
                    migration.get("updatedYaml") if migration else None),
                "updatedEtag": (
                    migration.get("updatedEtag") if migration else None),
                "previousEtag": (
                    migration.get("previousEtag") if migration else None),
                "pendingMigration": meta_data.get("pendingMigration"),
                "previousRegistration": meta_data.get(
                    "previousRegistration"),
                **rail_fields(meta_data),
            }

        # Poll registry
        from kodosumi.service.expose.registry import get_registration_status
        result = await get_registration_status(
            masumi,
            registration_id=reg_id,
            agent_identifier=agent_id,
            payment_source_type=meta_data.get("paymentSourceType"),
            registry_row_only=bool(meta_data.get("deregistrationState")),
        )
        queried_identity = (agent_id, reg_id)
        row, meta_data, result = await resume_active_deregistration(
            masumi, row, name, flow_url, meta_data, result,
            migration_lock(name, flow_url), expected_network=network,
        )
        if row is None or meta_data is None:
            return {"error": "Flow not found"}
        if row.get("network") != network:
            return {"error": "Network changed. Retry."}
        agent_id = meta_data.get("agentIdentifier")
        reg_id = meta_data.get("registrationId")
        if not agent_id and not reg_id:
            return {"state": "NotRegistered"}
        if (agent_id, reg_id) != queried_identity:
            return {"state": "Polling", "registrationId": reg_id}

        active_deregistration = await active_deregistration_response(
            row, name, flow_url, meta_data, result, migration)
        if active_deregistration is not None:
            return active_deregistration

        if not result:
            return {"state": "Polling", "registrationId": reg_id}

        reg_state = result.get("state", "Unknown")
        new_agent_id = result.get("agentIdentifier")

        # If confirmed, write agentIdentifier to YAML
        updated_yaml = None
        if reg_state == "RegistrationConfirmed" and new_agent_id:
            updated_yaml = await update_flow_meta(row, name, flow_url, {
                "agentIdentifier": new_agent_id,
            }, expected={
                "agentIdentifier": agent_id,
                "registrationId": reg_id,
            })

        tx = result.get("CurrentTransaction") or {}
        error_message = tx.get("errorMessage") or tx.get("error") or ""
        if not error_message:
            top_error = result.get("error") or ""
            if top_error and top_error != "{}":
                error_message = top_error

        return {
            "state": reg_state,
            "agentIdentifier": new_agent_id,
            "registrationId": result.get("id"),
            "errorMessage": error_message,
            "transaction": tx or None,
            "migration": migration,
            "pendingMigration": meta_data.get("pendingMigration"),
            "previousRegistration": meta_data.get("previousRegistration"),
            **flow_meta_update_fields(updated_yaml),
            **rail_fields(meta_data),
        }

    @post(
        "/deregister",
        summary="Deregister agent",
        description="Remove an agent from the Masumi on-chain registry. "
                    "Retains its identity until the burn confirms.",
        operation_id="registry_deregister",
    )
    async def deregister(
        self, name: str, data: dict, state: State
    ) -> dict:
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            raise ClientException(
                detail="No network configured", status_code=422)

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)

        flow_url = data.get("flow_url", "")
        if not flow_url:
            raise ClientException(
                detail="flow_url is required", status_code=422)
        try:
            action_etag = parse_flow_etag(data.get("meta_etag"))
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)
        meta_data = get_flow_meta(row, flow_url)
        if meta_data is None:
            raise ClientException(
                detail=f"Flow '{flow_url}' not found", status_code=404)

        agent_id = meta_data.get("agentIdentifier")
        if not agent_id:
            raise ClientException(
                detail="No agentIdentifier found. This flow is not registered.",
                status_code=422,
            )

        from kodosumi.service.expose.registry import (deregister_agent,
                                                      get_registration_status)
        async with migration_lock(name, flow_url):
            row = await db.get_expose(name)
            if not row:
                raise NotFoundException(detail=f"Expose '{name}' not found")
            if row.get("network") != network:
                raise ClientException(
                    detail="The expose network changed. Retry.",
                    status_code=409,
                )
            if (action_etag is not None
                    and float(row.get("updated") or 0) != action_etag):
                raise ClientException(
                    detail="The flow changed. Reload before deregistering.",
                    status_code=409,
                )
            meta_data = get_flow_meta(row, flow_url)
            if meta_data is None:
                raise ClientException(
                    detail=f"Flow '{flow_url}' not found", status_code=404)
            agent_id = meta_data.get("agentIdentifier")
            if not agent_id:
                raise ClientException(
                    detail="This flow is no longer registered.",
                    status_code=409,
                )
            if meta_data.get("pendingMigration"):
                raise ClientException(
                    detail="Wait for or cancel the pending migration first.",
                    status_code=409,
                )
            previous = meta_data.get("previousRegistration")
            if isinstance(previous, dict) and previous.get("agentIdentifier"):
                raise ClientException(
                    detail="Deregister the previous registration first.",
                    status_code=409,
                )

            saved_state = meta_data.get("deregistrationState")
            if saved_state in {
                    DEREGISTRATION_INTENT_STATE,
                    DEREGISTRATION_REQUESTED_STATE,
                    DEREGISTRATION_INITIATED_STATE}:
                return {
                    "success": True,
                    "state": (
                        DEREGISTRATION_REQUESTED_STATE
                        if saved_state == DEREGISTRATION_INTENT_STATE
                        else saved_state),
                    "agentIdentifier": agent_id,
                }

            # Save intent first. A lost POST response can then be recovered
            # by the poll endpoint without submitting a second burn.
            updated_yaml = await update_flow_meta(
                row,
                name,
                flow_url,
                active_deregistration_updates(
                    DEREGISTRATION_INTENT_STATE),
                expected={
                    "agentIdentifier": agent_id,
                    "registrationId": meta_data.get("registrationId"),
                    "pendingMigration": meta_data.get("pendingMigration"),
                    "previousRegistration": previous,
                    "deregistrationState": saved_state,
                },
            )
            if updated_yaml is None:
                raise ClientException(
                    detail="Could not save deregistration state.",
                    status_code=500,
                )
            intent_expected = {
                "agentIdentifier": agent_id,
                "registrationId": meta_data.get("registrationId"),
                "deregistrationState": DEREGISTRATION_INTENT_STATE,
            }

            try:
                result = await deregister_agent(masumi, agent_id)
            except Exception as e:
                failure_yaml = None
                try:
                    recovered = await get_registration_status(
                        masumi,
                        registration_id=meta_data.get("registrationId"),
                        agent_identifier=agent_id,
                        payment_source_type=meta_data.get(
                            "paymentSourceType"),
                        registry_row_only=True,
                    )
                except Exception:
                    recovered = None
                recovered_state = (
                    recovered.get("state") if recovered else None)
                if recovered_state in ACTIVE_DEREGISTRATION_STATES:
                    recovered_yaml = await update_flow_meta(
                        row,
                        name,
                        flow_url,
                        active_deregistration_updates(recovered_state),
                        expected=intent_expected,
                    )
                    if recovered_yaml is not None:
                        recovered_yaml = compose_flow_meta_updates(
                            updated_yaml, recovered_yaml)
                        return {
                            "success": True,
                            "state": recovered_state,
                            "agentIdentifier": (
                                None if recovered_state ==
                                "DeregistrationConfirmed" else agent_id),
                            **flow_meta_update_fields(recovered_yaml),
                        }
                elif recovered:
                    failure_yaml = await update_flow_meta(
                        row,
                        name,
                        flow_url,
                        active_deregistration_updates(
                            DEREGISTRATION_FAILED_STATE),
                        expected=intent_expected,
                    )
                failure_yaml = compose_flow_meta_updates(
                    updated_yaml, failure_yaml)
                raise ClientException(
                    detail=str(e),
                    status_code=502,
                    extra=flow_meta_update_fields(failure_yaml),
                )

            remote_state = result.get(
                "state", DEREGISTRATION_REQUESTED_STATE)
            response_state = (
                remote_state
                if remote_state in ACTIVE_DEREGISTRATION_STATES
                else DEREGISTRATION_FAILED_STATE
            )
            final_yaml = await update_flow_meta(
                row,
                name,
                flow_url,
                active_deregistration_updates(response_state),
                expected=intent_expected,
            )
            if final_yaml is not None:
                updated_yaml = compose_flow_meta_updates(
                    updated_yaml, final_yaml)
            else:
                response_state = DEREGISTRATION_REQUESTED_STATE

        return {
            "success": True,
            "state": response_state,
            "agentIdentifier": (
                None if response_state == "DeregistrationConfirmed"
                else agent_id),
            **flow_meta_update_fields(updated_yaml),
        }
