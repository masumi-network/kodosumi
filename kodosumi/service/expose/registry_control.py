"""
Controller for the Masumi registry endpoints of an expose.

Registers a flow as an agent, tracks the registration state, and lists the
selling wallets that decide whether the agent runs on V1 or V2.

All endpoints require operator role authentication.
"""

import logging
from typing import Optional

import yaml
import litestar
from litestar import get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException, NotFoundException

from kodosumi.service.jwt import operator_guard
from kodosumi.service.expose import db
from kodosumi.service.expose.flow_meta import get_flow_meta, update_flow_meta
from kodosumi.service.expose.migration import advance_migration
from kodosumi.service.expose.registration import (
    build_agent_fields, rail_fields, sumi_api_base_url)

logger = logging.getLogger(__name__)


class RegistryControl(litestar.Controller):
    """Controller for Masumi Registry integration endpoints."""

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
        """
        Check Masumi Registry status for a specific flow.

        Reads agentIdentifier/registrationId from the flow's meta YAML
        and queries the Masumi Registry API for current status.
        """
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

        # Parse meta to find the flow
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
            masumi, row, name, flow_url, meta_data, allow_burn=False)
        if migration and migration.get("updatedYaml"):
            row = await db.get_expose(name) or row
            meta_data = get_flow_meta(row, flow_url) or meta_data
            agent_id = meta_data.get("agentIdentifier")
            reg_id = meta_data.get("registrationId")

        # If agentIdentifier is in YAML, the agent IS registered on-chain.
        # Query registry for latest details but don't flip to "not registered"
        # if the API is temporarily unavailable.
        from kodosumi.service.expose.registry import get_registration_status
        try:
            result = await get_registration_status(
                masumi,
                registration_id=reg_id,
                agent_identifier=agent_id,
                payment_source_type=meta_data.get("paymentSourceType"),
            )
        except Exception as e:
            logger.warning("Registry API error for %s: %s", name, e)
            result = None

        if not result:
            if agent_id:
                # Trust YAML — agent was confirmed on-chain
                return {
                    "registered": True,
                    "state": "RegistrationConfirmed",
                    "agentIdentifier": agent_id,
                    "registrationId": reg_id,
                    "migration": migration,
                    **rail_fields(meta_data),
                }
            return {
                "registered": False,
                "state": "Polling",
                "registrationId": reg_id,
                "migration": migration,
                **rail_fields(meta_data),
            }

        # Backfill registrationId only — this is a one-time essential fix.
        # Do NOT sync other fields here as it changes the ETag and causes
        # 409 conflicts when the user tries to save the form.
        result_reg_id = result.get("id")
        if agent_id and not reg_id and result_reg_id and flow_url:
            await update_flow_meta(row, name, flow_url, {
                "registrationId": result_reg_id,
            })

        tx = result.get("CurrentTransaction") or {}
        # Error can be in CurrentTransaction.errorMessage or top-level error field
        error_message = tx.get("errorMessage") or tx.get("error") or ""
        if not error_message:
            top_error = result.get("error") or ""
            if top_error and top_error != "{}":
                error_message = top_error

        return {
            "registered": result.get("state") == "RegistrationConfirmed",
            "state": result.get("state", "Unknown"),
            "agentIdentifier": result.get("agentIdentifier") or agent_id,
            "registrationId": result_reg_id or reg_id,
            "name": result.get("name"),
            "transaction": tx or None,
            "errorMessage": error_message,
            "migration": migration,
            # The rail comes from the flow meta, not from the registry
            # response: it decides which button the operator gets next.
            **rail_fields(meta_data),
        }

    @post(
        "",
        summary="Register agent on Masumi",
        description="Register an agent flow on the Masumi on-chain registry. Reads display, description, tags, and pricing from the flow's meta YAML. Requires wallet_vkey and flow_url in request body.",
        operation_id="registry_register",
    )
    async def register(
        self, name: str, data: dict, state: State
    ) -> dict:
        """
        Register an agent flow on the Masumi on-chain registry.

        Reads display, description, tags, pricing from the flow's meta YAML.
        Requires wallet_vkey and flow_url in the request body.

        Body:
            flow_url: str - Flow URL path (e.g. /myapp/analyze)
            wallet_vkey: str - Selling wallet verification key
            pricing_type: str - "Free" or "Fixed" (optional, reads from YAML if not set)
            amount: float - Human-readable amount (optional, reads from YAML if not set)
            currency: str - "USDM" or "ADA" (optional, reads from YAML if not set)
        """
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            raise ClientException(detail="No network configured for this expose", status_code=422)

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)

        # Validate API connectivity first
        from kodosumi.service.expose.registry import (
            register_agent, pricing_yaml_to_registry, pricing_to_yaml_format,
            update_meta_yaml_field, list_wallets,
            registry_pricing_to_supported_sources,
            DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX,
            PAYMENT_SOURCE_TYPE_V1, PAYMENT_SOURCE_TYPE_V2,
        )

        # Quick health check to validate token
        try:
            wallets = await list_wallets(masumi)
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
            raise ClientException(detail="flow_url is required", status_code=422)
        if not wallet_vkey:
            raise ClientException(detail="wallet_vkey is required", status_code=422)

        # Validate wallet exists
        valid_vkeys = [w["walletVkey"] for w in wallets]
        if wallet_vkey not in valid_vkeys:
            raise ClientException(
                detail=f"Wallet '{wallet_vkey[:8]}...' not found. Available: {[v[:8] + '...' for v in valid_vkeys]}",
                status_code=422,
            )

        # The selected wallet decides the registration version: a wallet of
        # a Web3CardanoV2 payment source registers a V2 agent, everything
        # else stays on the V1 shape.
        selected_wallet = next(
            w for w in wallets if w["walletVkey"] == wallet_vkey)
        payment_source_type = (
            selected_wallet.get("paymentSourceType") or PAYMENT_SOURCE_TYPE_V1)
        is_v2 = payment_source_type == PAYMENT_SOURCE_TYPE_V2

        # Parse meta YAML — prefer live textarea content from frontend
        # over stale DB data, so unsaved edits are used for registration.
        frontend_yaml = data.get("meta_yaml", "")
        if frontend_yaml:
            try:
                parsed = yaml.safe_load(frontend_yaml)
                if not isinstance(parsed, dict):
                    raise ClientException(
                        detail="Invalid YAML format — expected a mapping (key: value pairs).",
                        status_code=422,
                    )
                meta_data = parsed
            except yaml.YAMLError as e:
                raise ClientException(
                    detail=f"YAML parse error in flow metadata: {e}",
                    status_code=422,
                )
        else:
            meta_data = get_flow_meta(row, flow_url)
        if meta_data is None:
            raise ClientException(detail=f"Flow '{flow_url}' not found", status_code=404)

        if meta_data.get("agentIdentifier"):
            raise ClientException(
                detail="This flow is already registered. Deregister first to re-register.",
                status_code=409,
            )

        # Build registration data from YAML
        fields = build_agent_fields(meta_data, name)

        # Determine pricing
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
            registry_pricing = pricing_yaml_to_registry(yaml_pricing, reg_network)
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

        # Register
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

        registration_id = result.get("id", "")

        # Update meta YAML with registrationId and pricing.
        # Use frontend_yaml as base so unsaved textarea edits are preserved.
        # V2 flows also record the rail and the advertised source index, which
        # every later payment and start_job response has to repeat. Both keys
        # are removed again for V1 so a re-registration cannot leave a stale
        # V2 marker behind.
        meta_updates = {
            "registrationId": registration_id,
            "agentPricing": yaml_pricing if pricing_type else meta_data.get("agentPricing"),
            "paymentSourceType": payment_source_type if is_v2 else None,
            "supportedPaymentSourceIndex":
                DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX if is_v2 else None,
        }
        updated_yaml = await update_flow_meta(
            row, name, flow_url, meta_updates,
            base_data=frontend_yaml or None,
        )

        return {
            "success": True,
            "registrationId": registration_id,
            "state": result.get("state", "RegistrationRequested"),
            "agentIdentifier": result.get("agentIdentifier"),
            "paymentSourceType": payment_source_type,
            "updatedYaml": updated_yaml,
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
        """
        Poll registration status and update meta YAML when confirmed.

        Called periodically by frontend JS after registration.

        Body:
            flow_url: str - Flow URL path
        """
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

        # This is the POST the panel repeats while it waits, so it is where
        # the replaced agent is burned once the operator asked for it.
        migration = await advance_migration(
            masumi, row, name, flow_url, meta_data, allow_burn=True)
        if migration and migration.get("updatedYaml"):
            row = await db.get_expose(name) or row
            meta_data = get_flow_meta(row, flow_url) or meta_data
            agent_id = meta_data.get("agentIdentifier")
            reg_id = meta_data.get("registrationId")

        if agent_id:
            return {
                "state": "RegistrationConfirmed",
                "agentIdentifier": agent_id,
                "migration": migration,
                "updatedYaml": migration.get("updatedYaml") if migration else None,
                **rail_fields(meta_data),
            }

        # Poll registry
        from kodosumi.service.expose.registry import get_registration_status
        result = await get_registration_status(
            masumi,
            registration_id=reg_id,
            agent_identifier=agent_id,
            payment_source_type=meta_data.get("paymentSourceType"),
        )

        if not result:
            return {"state": "Polling", "registrationId": reg_id}

        reg_state = result.get("state", "Unknown")
        new_agent_id = result.get("agentIdentifier")

        # If confirmed, write agentIdentifier to YAML
        updated_yaml = None
        if reg_state == "RegistrationConfirmed" and new_agent_id:
            updated_yaml = await update_flow_meta(row, name, flow_url, {
                "agentIdentifier": new_agent_id,
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
            "updatedYaml": updated_yaml,
            "migration": migration,
            **rail_fields(meta_data),
        }

    @post(
        "/deregister",
        summary="Deregister agent",
        description="Remove an agent from the Masumi on-chain registry. Clears agentIdentifier and registrationId from the flow's meta YAML.",
        operation_id="registry_deregister",
    )
    async def deregister(
        self, name: str, data: dict, state: State
    ) -> dict:
        """
        Deregister an agent from the on-chain registry.

        Body:
            flow_url: str - Flow URL path
        """
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            raise ClientException(detail="No network configured", status_code=422)

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)

        flow_url = data.get("flow_url", "")
        if not flow_url:
            raise ClientException(detail="flow_url is required", status_code=422)
        meta_data = get_flow_meta(row, flow_url)
        if meta_data is None:
            raise ClientException(detail=f"Flow '{flow_url}' not found", status_code=404)

        agent_id = meta_data.get("agentIdentifier")
        if not agent_id:
            raise ClientException(detail="No agentIdentifier found — not registered", status_code=422)

        # Burning the live agent while its replacement is still minting would
        # leave the flow unlisted on both rails.
        if meta_data.get("pendingMigration"):
            raise ClientException(
                detail="A migration to Web3CardanoV2 is waiting for "
                       "confirmation. Wait for it, or cancel the migration, "
                       "then deregister.",
                status_code=409,
            )

        from kodosumi.service.expose.registry import deregister_agent
        try:
            result = await deregister_agent(masumi, agent_id)
        except RuntimeError as e:
            raise ClientException(detail=str(e), status_code=502)

        # Remove agentIdentifier, registrationId and the V2 payment markers
        # from YAML. A later re-registration writes them again if it picks a
        # V2 wallet.
        await update_flow_meta(row, name, flow_url, {
            "agentIdentifier": None,
            "registrationId": None,
            "paymentSourceType": None,
            "supportedPaymentSourceIndex": None,
            "migrationError": None,
            # The panel offers a burn button for this record. Keeping it on
            # a flow that is no longer registered offers an action against
            # an agent the operator has already moved off.
            "previousRegistration": None,
        })

        return {
            "success": True,
            "state": result.get("state", "DeregistrationRequested"),
        }

class WalletsControl(litestar.Controller):
    """Controller for wallet listing endpoint."""

    path = "/expose/{name:str}/wallets"
    tags = ["Registry"]
    guards = [operator_guard]

    @get(
        "",
        summary="List wallets for expose network",
        description="List available selling wallets from Masumi Payment API for the expose's configured network.",
        operation_id="registry_wallets",
    )
    async def list_wallets(self, name: str, state: State) -> dict:
        """List available selling wallets for the expose's configured network."""
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            return {"wallets": [], "error": "No network configured. Set network first."}

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            return {"wallets": [], "error": str(e)}

        from kodosumi.service.expose.registry import list_wallets
        try:
            wallets = await list_wallets(masumi)
        except Exception as e:
            return {
                "wallets": [],
                "error": f"Cannot reach Masumi API: {e}. Check KODO_MASUMI token.",
            }

        if not wallets:
            return {
                "wallets": [],
                "error": f"No selling wallets found for network '{network}'. "
                         "Check your Masumi Payment API token and configuration.",
            }

        return {"wallets": wallets, "network": network}
