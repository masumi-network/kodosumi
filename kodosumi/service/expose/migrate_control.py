"""
Controller for moving a registered flow from Web3CardanoV1 to V2.

Masumi has no in place upgrade: the migration mints a second agent under
the V2 policy and keeps the V1 agent answering jobs until that mint is
confirmed. See kodosumi/service/expose/migration.py for the state it keeps
in the flow meta.

All endpoints require operator role authentication.
"""

import logging
from typing import Optional

import litestar
import yaml
from litestar import post
from litestar.datastructures import State
from litestar.exceptions import ClientException, NotFoundException

from kodosumi.service.jwt import operator_guard
from kodosumi.service.expose import db
from kodosumi.service.expose.flow_meta import get_flow_meta, update_flow_meta
from kodosumi.service.expose.migration import start_migration_updates
from kodosumi.service.expose.registration import (
    build_agent_fields, sumi_api_base_url)

logger = logging.getLogger(__name__)


class RegistryMigrateControl(litestar.Controller):
    """Controller for the V1 to V2 migration of a registered flow."""

    path = "/expose/{name:str}/registry/migrate"
    tags = ["Registry"]
    guards = [operator_guard]

    @post(
        "",
        summary="Migrate an agent to a Web3CardanoV2 payment source",
        description="Register the flow a second time on a Web3CardanoV2 selling wallet. The V1 agent keeps serving until the new mint confirms, then the flow switches over. Requires wallet_vkey and flow_url in the request body.",
        operation_id="registry_migrate",
    )
    async def migrate(self, name: str, data: dict, state: State) -> dict:
        """
        Start the migration of a registered flow to a V2 payment source.

        Body:
            flow_url: str - Flow URL path (e.g. /myapp/analyze)
            wallet_vkey: str - Selling wallet of a Web3CardanoV2 source
            deregister_previous: bool - burn the V1 agent once V2 confirms
            meta_yaml: str - live YAML of the flow (optional)
        """
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
            PAYMENT_SOURCE_TYPE_V2, list_wallets, pricing_yaml_to_registry,
            register_agent, registry_pricing_to_supported_sources,
        )

        flow_url = data.get("flow_url", "")
        wallet_vkey = data.get("wallet_vkey", "")
        if not flow_url:
            raise ClientException(detail="flow_url is required", status_code=422)
        if not wallet_vkey:
            raise ClientException(
                detail="wallet_vkey is required", status_code=422)

        frontend_yaml = data.get("meta_yaml", "")
        meta_data = _parse_live_yaml(frontend_yaml) if frontend_yaml \
            else get_flow_meta(row, flow_url)
        if meta_data is None:
            raise ClientException(
                detail=f"Flow '{flow_url}' not found", status_code=404)

        agent_id = meta_data.get("agentIdentifier")
        if not agent_id:
            raise ClientException(
                detail="This flow is not registered yet. Register it first.",
                status_code=409,
            )
        if meta_data.get("paymentSourceType") == PAYMENT_SOURCE_TYPE_V2:
            raise ClientException(
                detail="This flow already runs on a Web3CardanoV2 payment source.",
                status_code=409,
            )
        if meta_data.get("pendingMigration"):
            raise ClientException(
                detail="A migration is already waiting for confirmation.",
                status_code=409,
            )

        try:
            wallets = await list_wallets(masumi)
        except Exception as e:
            raise ClientException(
                detail=f"Cannot reach Masumi API: {e}. "
                       "Check KODO_MASUMI configuration.",
                status_code=502,
            )

        wallet = next(
            (w for w in wallets if w["walletVkey"] == wallet_vkey), None)
        if wallet is None:
            raise ClientException(
                detail=f"Wallet '{wallet_vkey[:8]}...' not found for network "
                       f"'{network}'.",
                status_code=422,
            )
        if wallet.get("paymentSourceType") != PAYMENT_SOURCE_TYPE_V2:
            raise ClientException(
                detail="Pick a selling wallet of a Web3CardanoV2 payment "
                       "source. The wallet decides the rail of the new agent.",
                status_code=422,
            )

        legacy_pricing = meta_data.get("agentPricing")
        if not legacy_pricing:
            raise ClientException(
                detail="No pricing configured. Add agentPricing to the flow "
                       "YAML before migrating.",
                status_code=422,
            )

        reg_network = masumi.registry_network
        try:
            supported_payment_sources = registry_pricing_to_supported_sources(
                pricing_yaml_to_registry(legacy_pricing, reg_network),
                reg_network,
                wallet.get("smartContractAddress", ""),
            )
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)

        fields = build_agent_fields(meta_data, name)
        try:
            result = await register_agent(
                masumi=masumi,
                name=fields["name"],
                description=fields["description"],
                api_base_url=sumi_api_base_url(
                    state["settings"].sumi_address, flow_url),
                tags=fields["tags"],
                pricing=None,
                author=fields["author"],
                capability=fields["capability"],
                legal=fields["legal"],
                wallet_vkey=wallet_vkey,
                supported_payment_sources=supported_payment_sources,
            )
        except RuntimeError as e:
            raise ClientException(detail=str(e), status_code=502)

        registration_id = result.get("id", "")
        deregister_previous = bool(data.get("deregister_previous"))
        updated_yaml = await update_flow_meta(
            row, name, flow_url,
            start_migration_updates(registration_id, deregister_previous),
            base_data=frontend_yaml or None,
        )

        return {
            "success": True,
            "registrationId": registration_id,
            "state": result.get("state", "RegistrationRequested"),
            "migrationState": "Polling",
            "deregisterPrevious": deregister_previous,
            "updatedYaml": updated_yaml,
        }

    @post(
        "/deregister-previous",
        summary="Deregister the agent a migration replaced",
        description="Burn the Web3CardanoV1 agent that a completed migration left on chain. Clears previousRegistration from the flow's meta YAML.",
        operation_id="registry_migrate_deregister_previous",
    )
    async def deregister_previous(
        self, name: str, data: dict, state: State
    ) -> dict:
        """
        Deregister the old agent a migration left on chain.

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
        meta_data = get_flow_meta(row, flow_url)
        if meta_data is None:
            raise ClientException(
                detail=f"Flow '{flow_url}' not found", status_code=404)

        previous = meta_data.get("previousRegistration") or {}
        previous_agent_id = previous.get("agentIdentifier")
        if not previous_agent_id:
            raise ClientException(
                detail="This flow has no previous registration on chain.",
                status_code=409,
            )

        from kodosumi.service.expose.registry import deregister_agent
        try:
            result = await deregister_agent(masumi, previous_agent_id)
        except RuntimeError as e:
            raise ClientException(detail=str(e), status_code=502)

        updated_yaml = await update_flow_meta(
            row, name, flow_url, {"previousRegistration": None})

        return {
            "success": True,
            "state": result.get("state", "DeregistrationRequested"),
            "updatedYaml": updated_yaml,
        }


def _parse_live_yaml(frontend_yaml: str) -> Optional[dict]:
    """Parse the YAML the operator sees, so unsaved edits are migrated too."""
    try:
        parsed = yaml.safe_load(frontend_yaml)
    except yaml.YAMLError as e:
        raise ClientException(
            detail=f"YAML parse error in flow metadata: {e}", status_code=422)
    if not isinstance(parsed, dict):
        raise ClientException(
            detail="Invalid YAML format. Expected a mapping (key: value pairs).",
            status_code=422,
        )
    return parsed
