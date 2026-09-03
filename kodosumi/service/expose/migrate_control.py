"""
Controller for moving a registered flow from Web3CardanoV1 to V2.

Masumi has no in place upgrade: the migration mints a second agent under
the V2 policy and keeps the V1 agent answering jobs until that mint is
confirmed. See kodosumi/service/expose/migration.py for the state it keeps
in the flow meta.

All endpoints require operator role authentication.
"""

import logging
from urllib.parse import urlparse

import litestar
from litestar import post
from litestar.datastructures import State
from litestar.exceptions import ClientException, NotFoundException

from kodosumi.service.expose import db
from kodosumi.service.expose.deregistration import ACTIVE_DEREGISTRATION_STATES
from kodosumi.service.expose.flow_meta import (flow_meta_update_fields,
                                               get_flow_meta, parse_flow_etag,
                                               update_flow_meta)
from kodosumi.service.expose.migration import (CANCEL_NOTICE,
                                               cancel_migration_updates,
                                               migration_lock,
                                               pending_migration,
                                               request_previous_deregistration,
                                               start_migration_updates)
from kodosumi.service.expose.registration import (build_agent_fields,
                                                  parse_live_yaml,
                                                  sumi_api_base_url)
from kodosumi.service.jwt import operator_guard

logger = logging.getLogger(__name__)


# What a host may hold. urlparse is not the parser that will fetch this
# url, and the two disagree outside these sets, so a character outside
# them is refused rather than guessed at. A host outside ASCII has to be
# entered in its punycode form, which is what DNS carries anyway.
HOST_CHARACTERS = set("abcdefghijklmnopqrstuvwxyz0123456789.-_")
IPV6_CHARACTERS = set("0123456789abcdef:.")


def _is_dotted_quad(host: str) -> bool:
    """True for the one spelling of an IPv4 address both parsers agree on.

    A client reads any host whose last label is a number as an address,
    and reads it in whatever base the digits imply. urlparse never does,
    so "0177.0.0.1" is a name here and 127.0.0.1 there. Only the plain
    decimal form with no leading zero means the same thing to both.
    """
    labels = host.split(".")
    if len(labels) != 4:
        return False
    for label in labels:
        if not label.isdigit():
            return False
        if label != "0" and label.startswith("0"):
            return False
        if int(label) > 255:
            return False
    return True


def _is_absolute_http_url(value: str) -> bool:
    """Accept only a url a buyer can actually call.

    A prefix check is not enough. "http://" alone, and a value with a space
    or a newline in it, both pass one and are useless on chain, where the
    mint cannot be taken back. The scheme is case insensitive per RFC 3986,
    so HTTPS:// is a legal url and must not be refused.

    A non-empty netloc is not enough either: "http://:8080" and "http://@"
    both have one and neither has a host. An invisible character is the
    same kind of trap, because a zero width space survives a copy out of a
    document and cannot be seen in the field afterwards.

    The parser here is not the parser that fetches the url. urlparse reads
    RFC 3986 and every client reads the WHATWG url standard, and the two
    disagree about a backslash, about a host outside ASCII, and about a
    host whose last label is a number. To urlparse the host of
    "https://good.com\\@evil.com" is evil.com; to a browser it is good.com.
    Approving one host and handing a buyer the other is worse than
    refusing, because the mint is permanent, so only the shapes both
    parsers read the same way are accepted.

    An underscore is not one of the disagreements. Both parsers keep it,
    RFC 2181 allows it, and compose deployments use it, so it stays.
    """
    if not value.isprintable() or any(c.isspace() for c in value):
        return False
    # A client reads a backslash in the authority as a separator. urlparse
    # reads it as one more character of the host.
    if "\\" in value:
        return False
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        parsed.port  # raises when the port is not a number in range
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https") or not host:
        return False
    # A user name or a password here would be minted into a public
    # registry entry that nobody can edit afterwards.
    if parsed.username or parsed.password:
        return False
    if parsed.netloc.startswith("["):
        return (set(host) <= IPV6_CHARACTERS
                and any(character.isalnum() for character in host))
    if not set(host) <= HOST_CHARACTERS:
        return False
    # A last label that is a number turns the whole host into an address
    # for a client and leaves it a name for urlparse. "0177.0.0.1" is a
    # public address here and loopback there, and "api.example.123" is a
    # host here and a parse error there, so a number may only appear in
    # the one form both read alike.
    # Strip the root dot before reading the last label. "example.com."
    # is a legitimate fully qualified name that both parsers keep, but
    # without this the last label is "" and the whole number rule below
    # is skipped: "http://0177.0.0.1." was minted, and a client resolves
    # it to 127.0.0.1.
    last_label = host.rstrip(".").rsplit(".", 1)[-1]
    if ((last_label.isdigit() or last_label.startswith("0x"))
            and not _is_dotted_quad(host)):
        return False
    # "http://." and "http://-" have a host by the parser's reckoning and
    # resolve for nobody, which is what a mistyped paste looks like.
    return any(character.isalnum() for character in host)


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
            api_base_url: str - url the new agent advertises (optional). It
                defaults to the url of the V1 listing, which is what a
                single deployment wants. Set it only when another
                deployment serves the V2 agent.
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
            select_wallet)

        flow_url = data.get("flow_url", "")
        wallet_vkey = data.get("wallet_vkey", "")
        if not flow_url:
            raise ClientException(
                detail="flow_url is required", status_code=422)
        if not wallet_vkey:
            raise ClientException(
                detail="wallet_vkey is required", status_code=422)

        # Read the raw value before any falsy default. "or" would turn 0,
        # False and [] into "" and drop them silently, on a field that
        # decides an irreversible mint.
        api_base_url_override = data.get("api_base_url")
        if api_base_url_override is None:
            api_base_url_override = ""
        if not isinstance(api_base_url_override, str):
            raise ClientException(
                detail="api_base_url must be a string",
                status_code=422,
            )
        api_base_url_override = api_base_url_override.strip()
        if api_base_url_override and not _is_absolute_http_url(
                api_base_url_override):
            raise ClientException(
                detail="api_base_url must be an absolute http:// or https:// "
                       "url with a host. The value is minted on chain and "
                       "kodosumi cannot update a registration, so a "
                       "malformed url cannot be corrected from here.",
                status_code=422,
            )

        deregister_previous = data.get("deregister_previous", False)
        if not isinstance(deregister_previous, bool):
            raise ClientException(
                detail="deregister_previous must be a boolean",
                status_code=422,
            )
        try:
            base_etag = parse_flow_etag(data.get("meta_etag"))
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)

        frontend_yaml = data.get("meta_yaml", "")
        if frontend_yaml and base_etag is None:
            raise ClientException(
                detail="meta_etag is required with meta_yaml",
                status_code=422,
            )
        meta_data = parse_live_yaml(frontend_yaml) if frontend_yaml \
            else get_flow_meta(row, flow_url)
        if meta_data is None:
            raise ClientException(
                detail=f"Flow '{flow_url}' not found", status_code=404)

        try:
            wallets = await list_wallets(masumi, require_complete=True)
        except Exception as e:
            raise ClientException(
                detail=f"Cannot reach Masumi API: {e}. "
                       "Check KODO_MASUMI configuration.",
                status_code=502,
            )

        try:
            wallet = select_wallet(wallets, wallet_vkey)
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)
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

        # The mint is irreversible, so the decision to make it and the
        # record of it are taken under the lock every other migration step
        # holds. Two clicks would otherwise both pass the checks below and
        # mint two V2 agents, of which only the second one is recorded.
        async with migration_lock(name, flow_url):
            row = await db.get_expose(name)
            if not row:
                raise NotFoundException(detail=f"Expose '{name}' not found")
            if row.get("network") != network:
                raise ClientException(
                    detail="The expose network changed. Retry.",
                    status_code=409,
                )
            if (base_etag is not None
                    and float(row.get("updated") or 0) != base_etag):
                raise ClientException(
                    detail="The flow changed. Reload before migrating.",
                    status_code=409,
                )
            # The saved metadata decides, never the request body: the
            # editor content is supplied by the caller, so a stale copy of
            # it would answer "nothing pending" for a running migration.
            saved_meta = get_flow_meta(row, flow_url)
            if saved_meta is None:
                raise ClientException(
                    detail=f"Flow '{flow_url}' not found", status_code=404)
            if not saved_meta.get("agentIdentifier"):
                raise ClientException(
                    detail="This flow is not registered yet. Register it "
                           "first.",
                    status_code=409,
                )
            if saved_meta.get("paymentSourceType") == PAYMENT_SOURCE_TYPE_V2:
                raise ClientException(
                    detail="This flow already runs on a Web3CardanoV2 "
                           "payment source.",
                    status_code=409,
                )
            if saved_meta.get("pendingMigration"):
                raise ClientException(
                    detail="A migration is already waiting for confirmation.",
                    status_code=409,
                )
            if (saved_meta.get("deregistrationState")
                    in ACTIVE_DEREGISTRATION_STATES):
                raise ClientException(
                    detail="Finish the active deregistration before migrating.",
                    status_code=409,
                )

            # The dialog tells the operator that the migration carries the
            # V1 listing over. Everything the mint advertises therefore has
            # to come from the saved metadata, not from an unsaved edit.
            fields = build_agent_fields(meta_data, name)
            if (fields != build_agent_fields(saved_meta, name)
                    or legacy_pricing != saved_meta.get("agentPricing")):
                raise ClientException(
                    detail="The editor holds unsaved changes to the agent "
                           "name, description, tags or pricing. Save the "
                           "flow first, so the new agent matches the V1 "
                           "listing it replaces.",
                    status_code=409,
                )

            reg_network = masumi.registry_network
            try:
                supported_payment_sources = (
                    registry_pricing_to_supported_sources(
                        pricing_yaml_to_registry(legacy_pricing, reg_network),
                        reg_network,
                        wallet.get("smartContractAddress", ""),
                    ))
            except ValueError as e:
                raise ClientException(detail=str(e), status_code=422)

            try:
                result = await register_agent(
                    masumi=masumi,
                    name=fields["name"],
                    description=fields["description"],
                    api_base_url=api_base_url_override or sumi_api_base_url(
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

            registration_id = result.get("id")
            if not registration_id:
                raise ClientException(
                    detail="Masumi accepted the migration but returned no "
                           "registration id. Check Masumi before retrying.",
                    status_code=502,
                )
            update_kwargs = (
                {"base_etag": base_etag}
                if base_etag is not None else {})
            updated_yaml = await update_flow_meta(
                row, name, flow_url,
                start_migration_updates(registration_id, deregister_previous),
                base_data=frontend_yaml or None,
                expected={
                    "agentIdentifier": saved_meta.get("agentIdentifier"),
                    "registrationId": saved_meta.get("registrationId"),
                    "pendingMigration": None,
                    "deregistrationState": saved_meta.get(
                        "deregistrationState"),
                },
                **update_kwargs,
            )
            if updated_yaml is None:
                raise ClientException(
                    detail=f"Migration registration {registration_id} was "
                           "submitted, but its state could not be saved. "
                           "Do not retry.",
                    status_code=500,
                )

        return {
            "success": True,
            "registrationId": registration_id,
            "state": result.get("state", "RegistrationRequested"),
            "migrationState": "Polling",
            "deregisterPrevious": deregister_previous,
            **flow_meta_update_fields(updated_yaml),
        }

    @post(
        "/cancel",
        summary="Stop waiting for a pending migration",
        description="Clear the pendingMigration record of a flow. Use it when the Web3CardanoV2 mint will not confirm. The flow keeps serving its current agent, and the migrate button comes back.",
        operation_id="registry_migrate_cancel",
    )
    async def cancel(self, name: str, data: dict, state: State) -> dict:
        """
        Give up on a pending migration and let the operator start over.

        The mint itself cannot be recalled. If it confirms later, the new
        agent stays on chain and has to be deregistered in the Masumi admin
        interface.

        Body:
            flow_url: str - Flow URL path
        """
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        flow_url = data.get("flow_url", "")
        if not flow_url:
            raise ClientException(
                detail="flow_url is required", status_code=422)
        try:
            action_etag = parse_flow_etag(data.get("meta_etag"))
        except ValueError as e:
            raise ClientException(detail=str(e), status_code=422)

        # A poll running right now can be confirming the very mint this
        # request gives up on. Take the lock it holds, so the cancel either
        # lands before the swap or finds nothing pending afterwards.
        async with migration_lock(name, flow_url):
            row = await db.get_expose(name)
            if not row:
                raise NotFoundException(detail=f"Expose '{name}' not found")
            if (action_etag is not None
                    and float(row.get("updated") or 0) != action_etag):
                raise ClientException(
                    detail="The flow changed. Reload before cancelling.",
                    status_code=409,
                )
            meta_data = get_flow_meta(row, flow_url)
            if meta_data is None:
                raise ClientException(
                    detail=f"Flow '{flow_url}' not found", status_code=404)
            if not pending_migration(meta_data):
                raise ClientException(
                    detail="This flow has no pending migration.",
                    status_code=409)

            updated_yaml = await update_flow_meta(
                row, name, flow_url, cancel_migration_updates(),
                expected={
                    "pendingMigration": meta_data.get("pendingMigration")})
            if updated_yaml is None:
                raise ClientException(
                    detail="Could not clear the pending migration.",
                    status_code=500,
                )
        return {
            "success": True,
            "notice": CANCEL_NOTICE,
            **flow_meta_update_fields(updated_yaml),
        }

    @post(
        "/deregister-previous",
        summary="Deregister the agent a migration replaced",
        description="Burn the Web3CardanoV1 agent that a completed migration left on chain. Tracks the request until the payment node confirms it.",
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

        result = await request_previous_deregistration(
            masumi, row, name, flow_url, meta_data,
            expected_network=network, expected_etag=action_etag)
        if result is None:
            raise ClientException(
                detail="This flow has no previous registration on chain.",
                status_code=409,
            )
        result_error = (
            result.get("deregisterError") or result.get("migrationError"))
        if result_error:
            raise ClientException(
                detail=result_error,
                status_code=result.get("statusCode", 502),
                extra={
                    key: result.get(key)
                    for key in (
                        "updatedYaml", "updatedEtag", "previousEtag")
                },
            )

        return {
            "success": True,
            # Without a deregistrationState only the intent was recorded:
            # the registry returned no row, and the next poll submits it.
            "state": (
                result.get("deregistrationState")
                or result.get("migrationState")
                or "DeregistrationRequested"),
            "updatedYaml": result.get("updatedYaml"),
            "updatedEtag": result.get("updatedEtag"),
            "previousEtag": result.get("previousEtag"),
        }
