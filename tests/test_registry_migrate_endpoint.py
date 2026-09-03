"""
Tests for the migrate endpoints of the admin panel.

Every branch here decides whether a second agent is minted on chain, so
each refusal is checked on its own: a mint that should not have happened
cannot be taken back.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from litestar.exceptions import ClientException, NotFoundException

from kodosumi.config import MasumiConfig
from kodosumi.service.expose.migrate_control import RegistryMigrateControl

MIGRATE = RegistryMigrateControl.migrate.fn
CANCEL = RegistryMigrateControl.cancel.fn
DEREGISTER_PREVIOUS = RegistryMigrateControl.deregister_previous.fn

V1 = "Web3CardanoV1"
V2 = "Web3CardanoV2"


def _state(network="Preprod") -> dict:
    settings = MagicMock()
    settings.sumi_address = "https://host"
    settings.get_masumi.return_value = MasumiConfig(
        network=network,
        base_url="https://test.masumi.network/api/v1",
        token="test-token",
        poll_interval=1.0,
    )
    return {"settings": settings}


def _row(network="Preprod") -> dict:
    return {
        "name": "expose", "network": network, "meta": "[]", "updated": 0.0}


def _meta(**overrides) -> dict:
    meta = {
        "display": "My Agent",
        "agentIdentifier": "v1-agent",
        "registrationId": "v1-reg",
        "agentPricing": [{"pricingType": "Free"}],
    }
    meta.update(overrides)
    return meta


def _v2_wallet(**overrides) -> dict:
    wallet = {
        "walletVkey": "vkey-v2",
        "walletAddress": "addr_test1w",
        "sourceId": "src1",
        "network": "Preprod",
        "paymentSourceType": V2,
        "smartContractAddress": "addr_test1contract",
    }
    wallet.update(overrides)
    return wallet


def _body(**overrides) -> dict:
    body = {
        "flow_url": "/flow",
        "wallet_vkey": "vkey-v2",
        "meta_etag": "0.0",
    }
    body.update(overrides)
    return body


def _patches(row=None, meta=None, wallets=None, register=None,
             deregister=None):
    """Patch everything the handlers reach outside their own module."""
    meta = _meta() if meta is None else meta

    async def migration_write(row, expose_name, flow_url, updates,
                              base_data=None, **kwargs):
        # A burn re-reads the flow after it records its intent, so the
        # write has to land in the dict that re-read returns.
        for key, value in updates.items():
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value
        return "display: My Agent\n"

    return {
        "init": patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock),
        "row": patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=_row() if row is None else row),
        "meta": patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            return_value=meta),
        "write": patch(
            "kodosumi.service.expose.migrate_control.update_flow_meta",
            new_callable=AsyncMock, return_value="display: My Agent\n"),
        "migration_meta": patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            return_value=meta),
        "migration_write": patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            new_callable=AsyncMock, side_effect=migration_write),
        "status": patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            return_value={"id": "v1-reg",
                          "agentIdentifier": "v1-agent",
                          "state": "RegistrationConfirmed"}),
        "wallets": patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_v2_wallet()] if wallets is None else wallets),
        "register": patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
            return_value=register or {"id": "v2-reg",
                                      "state": "RegistrationRequested"}),
        "deregister": patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
            **({"side_effect": deregister}
               if isinstance(deregister, Exception)
               else {"return_value": deregister or
                     {"state": "DeregistrationRequested"}})),
    }


async def _call_migrate(body=None, **patch_kwargs):
    mocks = _patches(**patch_kwargs)
    with mocks["init"], mocks["row"], mocks["meta"], \
            mocks["write"] as write, mocks["wallets"], \
            mocks["register"] as register, mocks["deregister"]:
        result = await MIGRATE(
            None, name="expose", data=_body(**(body or {})), state=_state())
    return result, write, register


class TestMigrateRefusals:
    """Each of these must stop before a second agent is minted."""

    @pytest.mark.asyncio
    async def test_unknown_expose(self):
        mocks = _patches(row=False)
        with mocks["init"], mocks["row"], mocks["register"] as register:
            with pytest.raises(NotFoundException):
                await MIGRATE(None, name="nope", data=_body(),
                              state=_state())
        register.assert_not_called()

    @pytest.mark.asyncio
    async def test_expose_without_a_network(self):
        mocks = _patches(row={"name": "expose", "network": ""})
        with mocks["init"], mocks["row"], mocks["register"] as register:
            with pytest.raises(ClientException) as err:
                await MIGRATE(None, name="expose", data=_body(),
                              state=_state())
        assert err.value.status_code == 422
        register.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_flow_url(self):
        with pytest.raises(ClientException) as err:
            await _call_migrate({"flow_url": ""})
        assert err.value.status_code == 422
        assert "flow_url" in err.value.detail

    @pytest.mark.asyncio
    async def test_missing_wallet(self):
        with pytest.raises(ClientException) as err:
            await _call_migrate({"wallet_vkey": ""})
        assert err.value.status_code == 422
        assert "wallet_vkey" in err.value.detail

    @pytest.mark.asyncio
    async def test_unregistered_flow(self):
        with pytest.raises(ClientException) as err:
            await _call_migrate(meta=_meta(agentIdentifier=None))
        assert err.value.status_code == 409
        assert "not registered" in err.value.detail

    @pytest.mark.asyncio
    async def test_flow_already_on_v2(self):
        with pytest.raises(ClientException) as err:
            await _call_migrate(meta=_meta(paymentSourceType=V2))
        assert err.value.status_code == 409
        assert "already runs" in err.value.detail

    @pytest.mark.asyncio
    async def test_migration_already_pending(self):
        pending = {"registrationId": "v2-reg", "paymentSourceType": V2}
        with pytest.raises(ClientException) as err:
            await _call_migrate(meta=_meta(pendingMigration=pending))
        assert err.value.status_code == 409
        assert "already waiting" in err.value.detail

    @pytest.mark.asyncio
    async def test_unknown_wallet(self):
        with pytest.raises(ClientException) as err:
            await _call_migrate(wallets=[])
        assert err.value.status_code == 422
        assert "not found" in err.value.detail

    @pytest.mark.asyncio
    async def test_v1_wallet_is_refused(self):
        # The wallet decides the rail, so a V1 wallet would mint another V1
        # agent and the migration would achieve nothing.
        with pytest.raises(ClientException) as err:
            await _call_migrate(
                wallets=[_v2_wallet(paymentSourceType=V1)])
        assert err.value.status_code == 422
        assert "Web3CardanoV2" in err.value.detail

    @pytest.mark.asyncio
    async def test_flow_without_pricing(self):
        with pytest.raises(ClientException) as err:
            await _call_migrate(meta=_meta(agentPricing=None))
        assert err.value.status_code == 422
        assert "No pricing" in err.value.detail

    @pytest.mark.asyncio
    async def test_wallet_without_a_contract_address(self):
        with pytest.raises(ClientException) as err:
            await _call_migrate(
                wallets=[_v2_wallet(smartContractAddress="")])
        assert err.value.status_code == 422
        assert "smart contract address" in err.value.detail

    @pytest.mark.asyncio
    async def test_pricing_written_as_a_mapping_is_a_422(self):
        # Hand edited YAML reaches this shape often. It used to raise a
        # KeyError inside the converter and answer 500.
        bad = _meta(agentPricing={"pricingType": "Fixed",
                                  "fixedPricing": [{"amount": "0"}]})
        with pytest.raises(ClientException) as err:
            await _call_migrate(meta=bad)
        assert err.value.status_code == 422

    @pytest.mark.asyncio
    async def test_fixed_pricing_without_an_amount_is_a_422(self):
        bad = _meta(agentPricing=[{"pricingType": "Fixed",
                                   "fixedPricing": []}])
        with pytest.raises(ClientException) as err:
            await _call_migrate(meta=bad)
        assert err.value.status_code == 422
        assert "Fixed pricing needs" in err.value.detail

    @pytest.mark.asyncio
    async def test_unsaved_pricing_edit_is_refused(self):
        """The dialog promises the V1 listing is carried over."""
        edited = _meta(agentPricing=[{
            "pricingType": "Fixed",
            "fixedPricing": [{"amount": "9999999", "unit": ""}]}])
        mocks = _patches()
        with mocks["init"], mocks["row"], mocks["meta"], mocks["write"], \
                mocks["wallets"], mocks["register"] as register:
            with pytest.raises(ClientException) as err:
                await MIGRATE(
                    None, name="expose",
                    data=_body(meta_yaml=yaml.dump(edited)),
                    state=_state())
        assert err.value.status_code == 409
        assert "Save the flow first" in err.value.detail
        register.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsaved_identity_edit_is_refused(self):
        # The name, description and tags are minted into the V2 entry too,
        # so guarding the pricing alone still lets the listings diverge.
        edited = _meta(display="A Different Name")
        mocks = _patches()
        with mocks["init"], mocks["row"], mocks["meta"], mocks["write"], \
                mocks["wallets"], mocks["register"] as register:
            with pytest.raises(ClientException) as err:
                await MIGRATE(
                    None, name="expose",
                    data=_body(meta_yaml=yaml.dump(edited)),
                    state=_state())
        assert err.value.status_code == 409
        register.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unrelated_saved_flow_still_migrates(self):
        # The editor content matches what is saved, so the mint proceeds.
        mocks = _patches()
        with mocks["init"], mocks["row"], mocks["meta"], mocks["write"], \
                mocks["wallets"], mocks["register"] as register:
            result = await MIGRATE(
                None, name="expose",
                data=_body(meta_yaml=yaml.dump(_meta())),
                state=_state())
        assert result["success"] is True
        register.assert_called_once()


    @pytest.mark.asyncio
    async def test_a_stale_editor_copy_cannot_hide_a_pending_migration(self):
        # meta_yaml is supplied by the caller. A copy taken before the
        # migration started carries no pendingMigration, and judging that
        # copy would let a second click mint a second V2 agent.
        saved = _meta(pendingMigration={"registrationId": "v2-reg"})
        stale = _meta()
        result, write, register = None, None, None
        mocks = _patches(meta=saved)
        with mocks["init"], mocks["row"], mocks["meta"], mocks["write"], \
                mocks["wallets"], mocks["register"] as register, \
                mocks["deregister"]:
            with pytest.raises(ClientException) as err:
                await MIGRATE(None, name="expose",
                              data=_body(meta_yaml=yaml.dump(stale)),
                              state=_state())
        assert err.value.status_code == 409
        assert "already waiting" in err.value.detail
        register.assert_not_called()

    @pytest.mark.asyncio
    async def test_waits_for_the_shared_migration_lock(self):
        # A second click must not mint a second agent while the first
        # request is still between its checks and its write.
        import asyncio

        from kodosumi.service.expose.migration import migration_lock

        mocks = _patches()
        lock = migration_lock("expose", "/flow")
        async with lock:
            with mocks["init"], mocks["row"], mocks["meta"], mocks["write"], \
                    mocks["wallets"], mocks["register"] as register, \
                    mocks["deregister"]:
                task = asyncio.create_task(MIGRATE(
                    None, name="expose", data=_body(), state=_state()))
                for _ in range(5):
                    await asyncio.sleep(0)
                assert not task.done()
                register.assert_not_called()
        with mocks["init"], mocks["row"], mocks["meta"], mocks["write"], \
                mocks["wallets"], mocks["register"] as register, \
                mocks["deregister"]:
            result = await task
        assert result["success"] is True


class TestMigrateSuccess:

    @pytest.mark.asyncio
    async def test_mints_a_v2_agent_and_records_it_as_pending(self):
        result, write, register = await _call_migrate()
        assert result["success"] is True
        assert result["registrationId"] == "v2-reg"
        assert result["migrationState"] == "Polling"

        sources = register.call_args.kwargs["supported_payment_sources"]
        assert sources == [{
            "chain": "Cardano",
            "network": "Preprod",
            "paymentSourceType": V2,
            "address": "addr_test1contract",
            "pricing": {"pricingType": "Free"},
        }]
        # V2 rejects the top level pricing field outright.
        assert register.call_args.kwargs["pricing"] is None

        updates = write.call_args.args[3]
        assert updates["pendingMigration"]["registrationId"] == "v2-reg"
        # The V1 agent keeps answering jobs until the new mint confirms.
        assert "agentIdentifier" not in updates

    @pytest.mark.asyncio
    async def test_the_deregister_choice_is_carried_into_the_record(self):
        _, write, _ = await _call_migrate({"deregister_previous": True})
        assert write.call_args.args[3][
            "pendingMigration"]["deregisterPrevious"] is True

    @pytest.mark.asyncio
    async def test_fixed_pricing_is_converted_into_a_v2_source(self):
        meta = _meta(agentPricing=[{
            "pricingType": "Fixed",
            "fixedPricing": [{"amount": "10000000", "unit": "lovelace"}]}])
        _, _, register = await _call_migrate(meta=meta)
        pricing = register.call_args.kwargs[
            "supported_payment_sources"][0]["pricing"]
        assert pricing == {
            "pricingType": "Fixed",
            "fixed": [{"asset": "", "amount": "10000000"}],
        }


class TestCancel:
    """A mint that will not confirm must not wedge the flow."""

    @pytest.mark.asyncio
    async def test_clears_the_pending_record(self):
        pending = {"registrationId": "v2-reg", "paymentSourceType": V2}
        mocks = _patches(meta=_meta(pendingMigration=pending))
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["write"] as write:
            result = await CANCEL(
                None, name="expose", data={"flow_url": "/flow"},
                state=_state())
        assert result["success"] is True
        assert write.call_args.args[3]["pendingMigration"] is None
        # A cancel is deliberate, so it leaves no standing error behind.
        assert write.call_args.args[3]["migrationError"] is None
        assert "cancelled" in result["notice"]

    @pytest.mark.asyncio
    async def test_refuses_when_nothing_is_pending(self):
        mocks = _patches()
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["write"] as write:
            with pytest.raises(ClientException) as err:
                await CANCEL(None, name="expose",
                             data={"flow_url": "/flow"}, state=_state())
        assert err.value.status_code == 409
        write.assert_not_called()

    @pytest.mark.asyncio
    async def test_requires_a_flow_url(self):
        mocks = _patches()
        with mocks["init"], mocks["row"], mocks["write"] as write:
            with pytest.raises(ClientException) as err:
                await CANCEL(None, name="expose", data={}, state=_state())
        assert err.value.status_code == 422
        write.assert_not_called()


class TestDeregisterPrevious:

    @pytest.mark.asyncio
    async def test_burns_the_replaced_agent(self):
        meta = _meta(
            agentIdentifier="v2-agent",
            paymentSourceType=V2,
            previousRegistration={"agentIdentifier": "v1-agent",
                                  "registrationId": "v1-reg"})
        mocks = _patches(meta=meta)
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["migration_meta"], \
                mocks["migration_write"] as write, mocks["status"], \
                mocks["deregister"] as deregister:
            result = await DEREGISTER_PREVIOUS(
                None, name="expose", data={"flow_url": "/flow"},
                state=_state())
        assert result["success"] is True
        assert deregister.call_args.args[1] == "v1-agent"
        previous = write.call_args.args[3]["previousRegistration"]
        assert previous["agentIdentifier"] == "v1-agent"
        assert previous["deregistrationState"] == "DeregistrationRequested"

    @pytest.mark.asyncio
    async def test_reports_polling_when_the_registry_has_no_row(self):
        # Only the intent is recorded then, and the next poll submits the
        # burn. The answer must not claim a submission that did not happen.
        meta = _meta(
            agentIdentifier="v2-agent",
            paymentSourceType=V2,
            previousRegistration={"agentIdentifier": "v1-agent",
                                  "registrationId": "v1-reg"})
        mocks = _patches(meta=meta)
        status = patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock, return_value=None)
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["migration_meta"], mocks["migration_write"], \
                status, mocks["deregister"] as deregister:
            result = await DEREGISTER_PREVIOUS(
                None, name="expose", data={"flow_url": "/flow"},
                state=_state())
        assert result["success"] is True
        assert result["state"] == "Polling"
        deregister.assert_not_called()

    @pytest.mark.asyncio
    async def test_refuses_without_a_previous_registration(self):
        mocks = _patches()
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["migration_meta"], \
                mocks["deregister"] as deregister:
            with pytest.raises(ClientException) as err:
                await DEREGISTER_PREVIOUS(
                    None, name="expose", data={"flow_url": "/flow"},
                    state=_state())
        assert err.value.status_code == 409
        deregister.assert_not_called()

    @pytest.mark.asyncio
    async def test_refuses_a_missing_flow_url(self):
        # Without the guard an omitted flow_url used to select the first
        # flow of the expose and burn that agent instead.
        meta = _meta(
            previousRegistration={"agentIdentifier": "v1-agent"})
        mocks = _patches(meta=meta)
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["migration_meta"], \
                mocks["deregister"] as deregister:
            with pytest.raises(ClientException) as err:
                await DEREGISTER_PREVIOUS(
                    None, name="expose", data={}, state=_state())
        assert err.value.status_code == 422
        deregister.assert_not_called()

    @pytest.mark.asyncio
    async def test_waits_for_the_shared_migration_lock(self):
        # The automatic burn on the poll endpoint holds this same lock. If
        # the manual one did not, both could burn the one agent, and this
        # endpoint's write would erase the error the other just recorded.
        import asyncio

        from kodosumi.service.expose.migration import migration_lock

        meta = _meta(previousRegistration={"agentIdentifier": "v1-agent"})
        mocks = _patches(meta=meta)
        lock = migration_lock("expose", "/flow")
        async with lock:
            with mocks["init"], mocks["row"], mocks["meta"], \
                    mocks["migration_meta"], mocks["migration_write"], \
                    mocks["status"], mocks["deregister"] as deregister:
                task = asyncio.create_task(DEREGISTER_PREVIOUS(
                    None, name="expose", data={"flow_url": "/flow"},
                    state=_state()))
                for _ in range(5):
                    await asyncio.sleep(0)
                assert not task.done()
                deregister.assert_not_called()
            assert lock.locked()
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["migration_meta"], mocks["migration_write"], \
                mocks["status"], mocks["deregister"] as deregister:
            result = await task
        assert result["success"] is True
        assert deregister.call_args.args[1] == "v1-agent"


class TestMigrateApiBaseUrl:
    """The url the new agent advertises is minted on chain.

    kodosumi never calls the payment node's update endpoint, so a url that
    is wrong at mint time cannot be corrected from here.
    """

    @pytest.mark.asyncio
    async def test_defaults_to_the_url_of_the_v1_listing(self):
        _, _, register = await _call_migrate()
        assert register.call_args.kwargs["api_base_url"] == \
            "https://host/sumi/flow"

    @pytest.mark.asyncio
    async def test_an_override_replaces_it(self):
        _, _, register = await _call_migrate(
            {"api_base_url": "https://v2.example.com/sumi/flow"})
        assert register.call_args.kwargs["api_base_url"] == \
            "https://v2.example.com/sumi/flow"

    @pytest.mark.asyncio
    async def test_an_empty_override_keeps_the_default(self):
        _, _, register = await _call_migrate({"api_base_url": "   "})
        assert register.call_args.kwargs["api_base_url"] == \
            "https://host/sumi/flow"

    @pytest.mark.asyncio
    async def test_a_relative_override_is_refused_before_the_mint(self):
        with pytest.raises(ClientException) as err:
            await _call_migrate({"api_base_url": "/sumi/flow"})
        assert err.value.status_code == 422
        assert "http://" in err.value.detail

    @pytest.mark.asyncio
    async def test_a_scheme_without_a_host_is_refused(self):
        # A truncated paste passes a startswith check and mints an agent
        # that no buyer can reach.
        for value in ("http://", "https://", "https:// evil.com",
                      "http://a\nb"):
            with pytest.raises(ClientException) as err:
                await _call_migrate({"api_base_url": value})
            assert err.value.status_code == 422, value

    @pytest.mark.asyncio
    async def test_an_uppercase_scheme_is_accepted(self):
        # RFC 3986 schemes are case insensitive, so this is a legal url.
        _, _, register = await _call_migrate(
            {"api_base_url": "HTTPS://Example.com/sumi/flow"})
        assert register.call_args.kwargs["api_base_url"] == \
            "HTTPS://Example.com/sumi/flow"

    @pytest.mark.asyncio
    async def test_a_non_string_is_refused_with_a_reason(self):
        for value in (42, ["https://a"], {"u": "x"}):
            with pytest.raises(ClientException) as err:
                await _call_migrate({"api_base_url": value})
            assert err.value.status_code == 422, value
