"""Regression tests for registry lifecycle findings from PR 88 review."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from litestar.exceptions import ClientException, NotFoundException

from kodosumi.config import MasumiConfig
from kodosumi.service.expose.migrate_control import RegistryMigrateControl
from kodosumi.service.expose.migration import (advance_migration,
                                               request_previous_deregistration)
from kodosumi.service.expose.registry_control import RegistryControl

REGISTER = RegistryControl.register.fn
DEREGISTER = RegistryControl.deregister.fn
POLL = RegistryControl.poll.fn
STATUS = RegistryControl.get_status.fn
MIGRATE = RegistryMigrateControl.migrate.fn
CANCEL = RegistryMigrateControl.cancel.fn
DEREGISTER_PREVIOUS = RegistryMigrateControl.deregister_previous.fn


def _config() -> MasumiConfig:
    return MasumiConfig(
        network="Preprod",
        base_url="https://test.masumi.network/api/v1",
        token="test-token",
        poll_interval=1.0,
    )


def _state() -> dict:
    settings = MagicMock()
    settings.sumi_address = "https://host"
    settings.get_masumi.return_value = _config()
    return {"settings": settings}


def _v2_with_previous(**previous_updates) -> dict:
    previous = {
        "agentIdentifier": "v1-agent",
        "registrationId": "v1-reg",
        "paymentSourceType": "Web3CardanoV1",
        "deregisterRequested": True,
    }
    previous.update(previous_updates)
    return {
        "agentIdentifier": "v2-agent",
        "registrationId": "v2-reg",
        "paymentSourceType": "Web3CardanoV2",
        "supportedPaymentSourceIndex": 0,
        "previousRegistration": previous,
    }


def _stored_meta(meta: dict):
    stored = dict(meta)

    async def write(_row, _name, _url, updates, base_data=None, expected=None):
        del base_data, expected
        for key, value in updates.items():
            if value is None:
                stored.pop(key, None)
            else:
                stored[key] = value
        return "updated: true\n"

    return stored, write


@pytest.mark.asyncio
async def test_previous_registration_stays_saved_while_burn_is_pending():
    stored, write = _stored_meta(_v2_with_previous())
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value={"meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "v1-reg",
                "agentIdentifier": "v1-agent",
                "state": "RegistrationConfirmed",
            },
        ),
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
            return_value={"state": "DeregistrationRequested"},
        ),
    ):
        result = await advance_migration(
            _config(), {}, "expose", "/flow", dict(stored), allow_burn=True
        )

    assert result["migrationState"] == "DeregistrationRequested"
    assert stored["previousRegistration"]["agentIdentifier"] == "v1-agent"
    assert stored["previousRegistration"]["deregistrationState"] == (
        "DeregistrationRequested"
    )


@pytest.mark.asyncio
async def test_previous_registration_clears_after_burn_confirmation():
    stored, write = _stored_meta(
        _v2_with_previous(deregistrationState="DeregistrationRequested")
    )
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value={"meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "v1-reg",
                "agentIdentifier": "v1-agent",
                "state": "DeregistrationConfirmed",
            },
        ) as status,
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        result = await advance_migration(
            _config(), {}, "expose", "/flow", dict(stored), allow_burn=True
        )

    assert result["migrationState"] == "MigrationConfirmed"
    assert "previousRegistration" not in stored
    assert status.call_args.kwargs["registration_id"] == "v1-reg"
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_pending_burn_is_polled_without_a_second_submission():
    stored, write = _stored_meta(
        _v2_with_previous(deregistrationState="DeregistrationInitiated")
    )
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value={"meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "v1-reg",
                "agentIdentifier": "v1-agent",
                "state": "DeregistrationInitiated",
            },
        ) as status,
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        result = await advance_migration(
            _config(), {}, "expose", "/flow", dict(stored), allow_burn=True
        )

    assert result["migrationState"] == "DeregistrationInitiated"
    assert stored["previousRegistration"]["deregistrationState"] == (
        "DeregistrationInitiated"
    )
    status.assert_awaited_once()
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_failed_burn_stays_reachable_for_a_manual_retry():
    stored, write = _stored_meta(
        _v2_with_previous(deregistrationState="DeregistrationRequested")
    )
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value={"meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "v1-reg",
                "agentIdentifier": "v1-agent",
                "state": "DeregistrationFailed",
                "error": "no collateral",
            },
        ),
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        result = await advance_migration(
            _config(), {}, "expose", "/flow", dict(stored), allow_burn=True
        )

    assert result["migrationState"] == "MigrationConfirmed"
    assert "no collateral" in result["deregisterError"]
    assert stored["previousRegistration"]["agentIdentifier"] == "v1-agent"
    assert stored["previousRegistration"]["deregisterRequested"] is False
    assert stored["previousRegistration"]["deregistrationState"] == (
        "DeregistrationFailed"
    )
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_manual_burn_keeps_previous_registration_until_confirmation():
    stored, write = _stored_meta(
        _v2_with_previous(deregisterRequested=False)
    )
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "v1-reg",
                "agentIdentifier": "v1-agent",
                "state": "RegistrationConfirmed",
            },
        ),
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
            return_value={"state": "DeregistrationRequested"},
        ) as deregister,
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new=deregister,
        ),
    ):
        result = await DEREGISTER_PREVIOUS(
            None,
            name="expose",
            data={"flow_url": "/flow"},
            state=_state(),
        )

    assert result["state"] == "DeregistrationRequested"
    assert stored["previousRegistration"]["agentIdentifier"] == "v1-agent"
    assert stored["previousRegistration"]["deregisterRequested"] is True
    assert stored["previousRegistration"]["deregistrationState"] == (
        "DeregistrationRequested"
    )
    deregister.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_conflict_recovers_the_pending_remote_request():
    stored, write = _stored_meta(_v2_with_previous())
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value={"meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            side_effect=[
                {
                    "id": "v1-reg",
                    "agentIdentifier": "v1-agent",
                    "state": "RegistrationConfirmed",
                },
                {
                    "id": "v1-reg",
                    "agentIdentifier": "v1-agent",
                    "state": "DeregistrationRequested",
                },
            ],
        ) as status,
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("current state: DeregistrationRequested"),
        ),
    ):
        result = await advance_migration(
            _config(), {}, "expose", "/flow", dict(stored), allow_burn=True
        )

    assert result["migrationState"] == "DeregistrationRequested"
    assert stored["previousRegistration"]["deregisterRequested"] is True
    assert stored["previousRegistration"]["deregistrationState"] == (
        "DeregistrationRequested"
    )
    assert status.await_count == 2


@pytest.mark.asyncio
async def test_manual_burn_stops_when_intent_cannot_be_saved():
    meta = _v2_with_previous(deregisterRequested=False)
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
        ) as status,
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        result = await request_previous_deregistration(
            _config(), row, "expose", "/flow", meta)

    assert "record" in result["deregisterError"].lower()
    status.assert_not_called()
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_active_v2_deregister_refuses_to_forget_retained_v1():
    meta = _v2_with_previous(deregisterRequested=False)
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value={"name": "expose",
                          "network": "Preprod", "meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        with pytest.raises(ClientException) as error:
            await DEREGISTER(
                None,
                name="expose",
                data={"flow_url": "/flow"},
                state=_state(),
            )

    assert error.value.status_code == 409
    assert "previous" in error.value.detail.lower()
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_active_deregister_retains_identity_until_confirmation():
    stored, write = _stored_meta({
        "agentIdentifier": "v2-agent",
        "registrationId": "v2-reg",
        "paymentSourceType": "Web3CardanoV2",
        "supportedPaymentSourceIndex": 0,
    })
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
            return_value={"state": "DeregistrationRequested"},
        ),
    ):
        result = await DEREGISTER(
            None,
            name="expose",
            data={"flow_url": "/flow"},
            state=_state(),
        )

    assert result["state"] == "DeregistrationRequested"
    assert stored.get("agentIdentifier") == "v2-agent"
    assert stored.get("registrationId") == "v2-reg"


@pytest.mark.asyncio
async def test_active_deregister_clears_identity_after_poll_confirmation():
    stored, write = _stored_meta({
        "agentIdentifier": "v2-agent",
        "registrationId": "v2-reg",
        "paymentSourceType": "Web3CardanoV2",
        "supportedPaymentSourceIndex": 0,
        "deregistrationState": "DeregistrationRequested",
    })
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.deregistration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "v2-reg",
                "agentIdentifier": "v2-agent",
                "state": "DeregistrationConfirmed",
            },
        ),
    ):
        result = await POLL(
            None,
            name="expose",
            data={"flow_url": "/flow"},
            state=_state(),
        )

    assert result["state"] == "DeregistrationConfirmed"
    assert "agentIdentifier" not in stored
    assert "registrationId" not in stored
    assert "deregistrationState" not in stored


@pytest.mark.asyncio
async def test_active_deregister_can_retry_after_a_rejected_request():
    stored, write = _stored_meta({
        "agentIdentifier": "v2-agent",
        "registrationId": "v2-reg",
        "paymentSourceType": "Web3CardanoV2",
    })
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("request rejected"),
        ),
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "v2-reg",
                "agentIdentifier": "v2-agent",
                "state": "RegistrationConfirmed",
            },
        ),
    ):
        with pytest.raises(ClientException) as error:
            await DEREGISTER(
                None,
                name="expose",
                data={"flow_url": "/flow"},
                state=_state(),
            )

    assert error.value.status_code == 502
    assert stored["deregistrationState"] == "DeregistrationFailed"


@pytest.mark.asyncio
async def test_burn_status_must_belong_to_the_previous_agent():
    stored, write = _stored_meta(
        _v2_with_previous(deregistrationState="DeregistrationRequested")
    )
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value={"meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "other-reg",
                "agentIdentifier": "other-agent",
                "state": "DeregistrationConfirmed",
            },
        ),
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        await advance_migration(
            _config(), {}, "expose", "/flow", dict(stored), allow_burn=True
        )

    assert stored.get("previousRegistration", {}).get(
        "agentIdentifier"
    ) == "v1-agent"
    deregister.assert_not_called()


@pytest.mark.parametrize(
    "remote_state",
    ["DeregistrationConfirmed", "DeregistrationFailed"],
)
@pytest.mark.asyncio
async def test_burn_stays_nonterminal_when_terminal_state_cannot_be_saved(
    remote_state,
):
    meta = _v2_with_previous(
        deregistrationState="DeregistrationRequested"
    )
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value={"meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "v1-reg",
                "agentIdentifier": "v1-agent",
                "state": remote_state,
                "error": "burn failed" if remote_state.endswith("Failed") else None,
            },
        ),
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        result = await advance_migration(
            _config(), {}, "expose", "/flow", meta, allow_burn=True
        )

    assert result["migrationState"] != "MigrationConfirmed"
    deregister.assert_not_called()


def _selling_wallet(payment_source_type="Web3CardanoV1") -> dict:
    return {
        "walletVkey": "vkey1",
        "paymentSourceType": payment_source_type,
        "smartContractAddress": "addr_test1contract",
    }


def _registration_meta(**updates) -> dict:
    meta = {
        "display": "Agent",
        "agentPricing": [{"pricingType": "Free"}],
    }
    meta.update(updates)
    return meta


@pytest.mark.asyncio
async def test_stale_registration_yaml_cannot_mint_a_second_agent():
    row = {
        "name": "expose", "network": "Preprod", "meta": "[]",
        "updated": 10.0,
    }
    saved = _registration_meta(registrationId="existing-reg")
    stale_yaml = "display: Agent\nagentPricing:\n- pricingType: Free\n"
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=saved,
        ),
        patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_selling_wallet()],
        ),
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
        ) as register,
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={"id": "existing-reg", "state": "RegistrationRequested"},
        ),
    ):
        with pytest.raises(ClientException) as error:
            await REGISTER(
                None,
                name="expose",
                data={
                    "flow_url": "/flow",
                    "wallet_vkey": "vkey1",
                    "meta_yaml": stale_yaml,
                    "meta_etag": "10.0",
                },
                state=_state(),
            )

    assert error.value.status_code == 409
    register.assert_not_called()


@pytest.mark.asyncio
async def test_registration_reports_the_remote_id_when_state_save_fails():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=_registration_meta(),
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_selling_wallet()],
        ),
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
            return_value={"id": "remote-reg"},
        ),
    ):
        with pytest.raises(ClientException) as error:
            await REGISTER(
                None,
                name="expose",
                data={"flow_url": "/flow", "wallet_vkey": "vkey1"},
                state=_state(),
            )

    assert error.value.status_code == 500
    assert "remote-reg" in error.value.detail
    assert "Do not retry" in error.value.detail


@pytest.mark.asyncio
async def test_migration_reports_the_remote_id_when_state_save_fails():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    meta = _registration_meta(
        agentIdentifier="v1-agent",
        registrationId="v1-reg",
    )
    with (
        patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.update_flow_meta",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_selling_wallet("Web3CardanoV2")],
        ),
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
            return_value={"id": "remote-v2-reg"},
        ),
    ):
        with pytest.raises(ClientException) as error:
            await MIGRATE(
                None,
                name="expose",
                data={"flow_url": "/flow", "wallet_vkey": "vkey1"},
                state=_state(),
            )

    assert error.value.status_code == 500
    assert "remote-v2-reg" in error.value.detail
    assert "Do not retry" in error.value.detail


@pytest.mark.asyncio
async def test_cancel_does_not_claim_success_when_state_save_fails():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    meta = _registration_meta(
        agentIdentifier="v1-agent",
        pendingMigration={"registrationId": "v2-reg"},
    )
    with (
        patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.update_flow_meta",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        with pytest.raises(ClientException) as error:
            await CANCEL(
                None,
                name="expose",
                data={"flow_url": "/flow"},
                state=_state(),
            )

    assert error.value.status_code == 500


@pytest.mark.parametrize(
    "guarded_state",
    [
        {"pendingMigration": {"registrationId": "v2-reg"}},
        {"previousRegistration": {"agentIdentifier": "v1-agent"}},
    ],
)
@pytest.mark.asyncio
async def test_active_deregister_rechecks_guards_inside_lock(guarded_state):
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    initial = _registration_meta(agentIdentifier="active-agent")
    refreshed = {**initial, **guarded_state}
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            side_effect=[initial, refreshed],
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
        ) as write,
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        with pytest.raises(ClientException) as error:
            await DEREGISTER(
                None,
                name="expose",
                data={"flow_url": "/flow"},
                state=_state(),
            )

    assert error.value.status_code == 409
    write.assert_not_called()
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_poll_submits_a_saved_deregistration_intent():
    stored, write = _stored_meta(_registration_meta(
        agentIdentifier="active-agent",
        registrationId="active-reg",
        deregistrationState="DeregistrationIntent",
    ))
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    confirmed = {
        "id": "active-reg",
        "agentIdentifier": "active-agent",
        "state": "RegistrationConfirmed",
    }
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.deregistration.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.deregistration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value=confirmed,
        ),
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
            return_value={"state": "DeregistrationRequested"},
        ) as deregister,
    ):
        result = await POLL(
            None,
            name="expose",
            data={"flow_url": "/flow"},
            state=_state(),
        )

    assert result["state"] == "DeregistrationRequested"
    assert stored["deregistrationState"] == "DeregistrationRequested"
    deregister.assert_awaited_once_with(_config(), "active-agent")


@pytest.mark.asyncio
async def test_poll_retries_an_intent_after_prior_remote_failure():
    stored, write = _stored_meta(_registration_meta(
        agentIdentifier="active-agent",
        registrationId="active-reg",
        deregistrationState="DeregistrationIntent",
    ))
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    failed = {
        "id": "active-reg",
        "agentIdentifier": "active-agent",
        "state": "DeregistrationFailed",
    }
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.deregistration.get_flow_meta",
            side_effect=lambda _row, _url: dict(stored),
        ),
        patch(
            "kodosumi.service.expose.deregistration.update_flow_meta",
            side_effect=write,
        ),
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value=failed,
        ),
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
            return_value={"state": "DeregistrationRequested"},
        ) as deregister,
    ):
        result = await POLL(
            None,
            name="expose",
            data={"flow_url": "/flow"},
            state=_state(),
        )

    assert result["state"] == "DeregistrationRequested"
    deregister.assert_awaited_once_with(_config(), "active-agent")


@pytest.mark.asyncio
async def test_second_deregister_request_defers_a_saved_intent_to_poll():
    meta = _registration_meta(
        agentIdentifier="active-agent",
        registrationId="active-reg",
        deregistrationState="DeregistrationIntent",
    )
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
        ) as write,
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        result = await DEREGISTER(
            None,
            name="expose",
            data={"flow_url": "/flow"},
            state=_state(),
        )

    assert result["state"] == "DeregistrationRequested"
    write.assert_not_called()
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_registration_status_matches_both_saved_identifiers():
    from kodosumi.service.expose.registry import get_registration_status

    urls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"Assets": [
                {
                    "id": "wrong-reg",
                    "agentIdentifier": "active-agent",
                    "state": "DeregistrationConfirmed",
                },
                {
                    "id": "active-reg",
                    "agentIdentifier": "active-agent",
                    "state": "DeregistrationRequested",
                },
            ]}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, headers):
            del headers
            urls.append(url)
            return Response()

    with patch(
        "kodosumi.service.expose.registry.HTTPXClient",
        new=Client,
    ):
        result = await get_registration_status(
            _config(),
            registration_id="active-reg",
            agent_identifier="active-agent",
        )

    assert result["id"] == "active-reg"
    assert all("agent-identifier" not in url for url in urls)


@pytest.mark.asyncio
async def test_stale_registration_etag_stops_before_remote_mint():
    initial = {
        "name": "expose", "network": "Preprod", "meta": "[]",
        "updated": 10.0,
    }
    changed = {**initial, "updated": 11.0}
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            side_effect=[initial, changed],
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=_registration_meta(),
        ),
        patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_selling_wallet()],
        ),
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
        ) as register,
    ):
        with pytest.raises(ClientException) as error:
            await REGISTER(
                None,
                name="expose",
                data={
                    "flow_url": "/flow",
                    "wallet_vkey": "vkey1",
                    "meta_yaml": yaml.dump(_registration_meta()),
                    "meta_etag": "10.0",
                },
                state=_state(),
            )

    assert error.value.status_code == 409
    register.assert_not_called()


@pytest.mark.asyncio
async def test_failed_registration_can_be_retried():
    row = {
        "name": "expose", "network": "Preprod", "meta": "[]",
        "updated": 10.0,
    }
    meta = _registration_meta(registrationId="failed-reg")
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
            return_value="registrationId: retry-reg\n",
        ),
        patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_selling_wallet()],
        ),
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={"id": "failed-reg", "state": "RegistrationFailed"},
        ),
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
            return_value={"id": "retry-reg", "state": "RegistrationRequested"},
        ) as register,
    ):
        result = await REGISTER(
            None,
            name="expose",
            data={"flow_url": "/flow", "wallet_vkey": "vkey1"},
            state=_state(),
        )

    assert result["registrationId"] == "retry-reg"
    register.assert_awaited_once()


@pytest.mark.asyncio
async def test_migration_refuses_an_active_deregistration():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    meta = _registration_meta(
        agentIdentifier="active-agent",
        registrationId="active-reg",
        deregistrationState="DeregistrationRequested",
    )
    with (
        patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_selling_wallet("Web3CardanoV2")],
        ),
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
        ) as register,
    ):
        with pytest.raises(ClientException) as error:
            await MIGRATE(
                None,
                name="expose",
                data={"flow_url": "/flow", "wallet_vkey": "vkey1"},
                state=_state(),
            )

    assert error.value.status_code == 409
    register.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_active_burn_uses_the_saved_identity_as_guard():
    from kodosumi.service.expose.deregistration import (
        active_deregistration_response,
    )

    meta = {
        "agentIdentifier": "active-agent",
        "registrationId": "active-reg",
        "deregistrationState": "DeregistrationRequested",
    }
    with patch(
        "kodosumi.service.expose.deregistration.update_flow_meta",
        new_callable=AsyncMock,
        return_value="state: cleared\n",
    ) as write:
        await active_deregistration_response(
            {},
            "expose",
            "/flow",
            meta,
            {
                "id": "active-reg",
                "agentIdentifier": "active-agent",
                "state": "DeregistrationConfirmed",
            },
        )

    assert write.call_args.kwargs["expected"] == meta


@pytest.mark.asyncio
async def test_swap_and_burn_return_one_composed_etag_chain():
    from kodosumi.service.expose.flow_meta import UpdatedFlowYaml

    meta = _v2_with_previous()
    migrating = {
        **meta,
        "pendingMigration": {"registrationId": "new-reg"},
    }
    first = UpdatedFlowYaml("swap: true\n", 10.0, 11.0)
    last = UpdatedFlowYaml("burn: true\n", 11.0, 12.0)
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value={"meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            side_effect=[migrating, meta],
        ),
        patch(
            "kodosumi.service.expose.migration._confirm_mint",
            new_callable=AsyncMock,
            return_value={
                "migrationState": "MigrationConfirmed",
                "updatedYaml": str(first),
                "updatedEtag": first.etag,
                "previousEtag": first.previous_etag,
            },
        ),
        patch(
            "kodosumi.service.expose.migration._burn_previous",
            new_callable=AsyncMock,
            return_value={
                "migrationState": "DeregistrationRequested",
                "updatedYaml": str(last),
                "updatedEtag": last.etag,
                "previousEtag": last.previous_etag,
            },
        ),
    ):
        result = await advance_migration(
            _config(), {}, "expose", "/flow", migrating, allow_burn=True)

    assert result["updatedYaml"] == str(last)
    assert result["previousEtag"] == "10.0"
    assert result["updatedEtag"] == "12.0"


@pytest.mark.asyncio
async def test_resolved_previous_id_and_burn_return_one_etag_chain():
    from kodosumi.service.expose.flow_meta import UpdatedFlowYaml
    from kodosumi.service.expose.migration import _burn_previous

    meta = _v2_with_previous(registrationId=None)
    first = UpdatedFlowYaml("resolved: true\n", 10.0, 11.0)
    last = UpdatedFlowYaml("requested: true\n", 11.0, 12.0)
    with (
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "resolved-reg",
                "agentIdentifier": "v1-agent",
                "state": "RegistrationConfirmed",
            },
        ),
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
            return_value={
                "id": "resolved-reg",
                "state": "DeregistrationRequested",
            },
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            new_callable=AsyncMock,
            side_effect=[first, last],
        ),
    ):
        result = await _burn_previous(
            _config(), {}, "expose", "/flow", meta)

    assert result["updatedYaml"] == str(last)
    assert result["previousEtag"] == "10.0"
    assert result["updatedEtag"] == "12.0"


@pytest.mark.asyncio
async def test_manual_burn_intent_and_result_return_one_etag_chain():
    from kodosumi.service.expose.flow_meta import UpdatedFlowYaml

    meta = _v2_with_previous(deregisterRequested=False)
    first = UpdatedFlowYaml("intent: true\n", 10.0, 11.0)
    last = {
        "migrationState": "DeregistrationRequested",
        "updatedYaml": "requested: true\n",
        "updatedEtag": "12.0",
        "previousEtag": "11.0",
    }
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value={"meta": "[]"},
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.migration.update_flow_meta",
            new_callable=AsyncMock,
            return_value=first,
        ),
        patch(
            "kodosumi.service.expose.migration._burn_previous",
            new_callable=AsyncMock,
            return_value=last,
        ),
    ):
        result = await request_previous_deregistration(
            _config(), {}, "expose", "/flow", meta)

    assert result["updatedYaml"] == "requested: true\n"
    assert result["previousEtag"] == "10.0"
    assert result["updatedEtag"] == "12.0"


@pytest.mark.asyncio
async def test_get_status_composes_migration_and_id_backfill_etags():
    from kodosumi.service.expose.flow_meta import UpdatedFlowYaml

    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    meta = _registration_meta(agentIdentifier="active-agent")
    migration = {
        "migrationState": "RegistrationFailed",
        "updatedYaml": "migration: failed\n",
        "updatedEtag": "11.0",
        "previousEtag": "10.0",
    }
    backfill = UpdatedFlowYaml("registrationId: active-reg\n", 11.0, 12.0)
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry_control.advance_migration",
            new_callable=AsyncMock,
            return_value=migration,
        ),
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "active-reg",
                "agentIdentifier": "active-agent",
                "state": "RegistrationFailed",
            },
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
            return_value=backfill,
        ) as write,
    ):
        result = await STATUS(
            None,
            name="expose",
            flow_url="/flow",
            state=_state(),
        )

    assert result["updatedYaml"] == str(backfill)
    assert result["previousEtag"] == "10.0"
    assert result["updatedEtag"] == "12.0"
    assert write.await_args.kwargs["expected"] == {
        "agentIdentifier": "active-agent",
        "registrationId": None,
    }


@pytest.mark.asyncio
async def test_active_deregister_error_exposes_saved_etag_chain():
    from kodosumi.service.expose.flow_meta import UpdatedFlowYaml

    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    meta = _registration_meta(
        agentIdentifier="active-agent",
        registrationId="active-reg",
    )
    intent = UpdatedFlowYaml("intent: true\n", 10.0, 11.0)
    failed = UpdatedFlowYaml("failed: true\n", 11.0, 12.0)
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
            side_effect=[intent, failed],
        ),
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("request rejected"),
        ),
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "active-reg",
                "agentIdentifier": "active-agent",
                "state": "RegistrationConfirmed",
            },
        ),
    ):
        with pytest.raises(ClientException) as error:
            await DEREGISTER(
                None,
                name="expose",
                data={"flow_url": "/flow"},
                state=_state(),
            )

    assert error.value.extra == {
        "updatedYaml": str(failed),
        "updatedEtag": "12.0",
        "previousEtag": "10.0",
    }


@pytest.mark.asyncio
async def test_deleted_expose_stops_registration_before_remote_mint():
    row = {
        "name": "expose", "network": "Preprod", "meta": "[]",
        "updated": 10.0,
    }
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            side_effect=[row, None],
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=_registration_meta(),
        ),
        patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_selling_wallet()],
        ),
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
        ) as register,
    ):
        with pytest.raises(NotFoundException):
            await REGISTER(
                None,
                name="expose",
                data={"flow_url": "/flow", "wallet_vkey": "vkey1"},
                state=_state(),
            )

    register.assert_not_called()


@pytest.mark.asyncio
async def test_deleted_expose_stops_active_deregistration_before_remote_burn():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    meta = _registration_meta(
        agentIdentifier="active-agent",
        registrationId="active-reg",
    )
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            side_effect=[row, None],
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        with pytest.raises(NotFoundException):
            await DEREGISTER(
                None,
                name="expose",
                data={"flow_url": "/flow"},
                state=_state(),
            )

    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_deleted_expose_stops_migration_before_remote_mint():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    meta = _registration_meta(
        agentIdentifier="active-agent",
        registrationId="active-reg",
        paymentSourceType="Web3CardanoV1",
    )
    with (
        patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            side_effect=[row, None],
        ),
        patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_selling_wallet("Web3CardanoV2")],
        ),
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
        ) as register,
    ):
        with pytest.raises(NotFoundException):
            await MIGRATE(
                None,
                name="expose",
                data={"flow_url": "/flow", "wallet_vkey": "vkey1"},
                state=_state(),
            )

    register.assert_not_called()


@pytest.mark.asyncio
async def test_deleted_expose_stops_automatic_previous_burn():
    meta = _v2_with_previous()
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
        ) as status,
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        result = await advance_migration(
            _config(), {}, "expose", "/flow", meta, allow_burn=True)

    assert result is None
    status.assert_not_called()
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_active_deregister_intent_guards_the_saved_identity():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    meta = _registration_meta(
        agentIdentifier="active-agent",
        registrationId="active-reg",
    )
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
            return_value="intent: true\n",
        ) as write,
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
            return_value={"state": "DeregistrationRequested"},
        ),
    ):
        await DEREGISTER(
            None,
            name="expose",
            data={"flow_url": "/flow"},
            state=_state(),
        )

    assert write.await_args_list[0].kwargs["expected"] == {
        "agentIdentifier": "active-agent",
        "registrationId": "active-reg",
        "pendingMigration": None,
        "previousRegistration": None,
        "deregistrationState": None,
    }


@pytest.mark.asyncio
async def test_network_change_stops_automatic_previous_burn():
    meta = _v2_with_previous()
    fresh = {"name": "expose", "network": "Mainnet", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value=fresh,
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
        ) as status,
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        result = await advance_migration(
            _config(), {}, "expose", "/flow", meta, allow_burn=True,
            expected_network="Preprod",
        )

    assert result["migrationState"] == "Polling"
    assert "network changed" in result["migrationError"].lower()
    status.assert_not_called()
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_network_change_stops_manual_previous_burn():
    meta = _v2_with_previous(deregisterRequested=False)
    fresh = {"name": "expose", "network": "Mainnet", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value=fresh,
        ),
        patch(
            "kodosumi.service.expose.migration.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
        ) as status,
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        result = await request_previous_deregistration(
            _config(), {}, "expose", "/flow", meta,
            expected_network="Preprod",
        )

    assert "network changed" in result["deregisterError"].lower()
    status.assert_not_called()
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_stale_migration_etag_stops_before_remote_mint():
    initial = {
        "name": "expose", "network": "Preprod", "meta": "[]",
        "updated": 10.0,
    }
    fresh = {**initial, "updated": 11.0}
    meta = _registration_meta(
        agentIdentifier="v1-agent",
        registrationId="v1-reg",
        paymentSourceType="Web3CardanoV1",
    )
    with (
        patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            side_effect=[initial, fresh],
        ),
        patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            return_value=meta,
        ),
        patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_selling_wallet("Web3CardanoV2")],
        ),
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
            return_value={"id": "unexpected-v2-reg"},
        ) as register,
    ):
        with pytest.raises(ClientException) as error:
            await MIGRATE(
                None,
                name="expose",
                data={
                    "flow_url": "/flow",
                    "wallet_vkey": "vkey1",
                    "meta_yaml": yaml.dump(meta),
                    "meta_etag": "10.0",
                },
                state=_state(),
            )

    assert error.value.status_code == 409
    register.assert_not_called()


@pytest.mark.asyncio
async def test_manual_previous_burn_does_not_hide_a_preflight_error():
    with (
        patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            return_value={
                "name": "expose", "network": "Preprod", "meta": "[]",
            },
        ),
        patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            return_value=_v2_with_previous(),
        ),
        patch(
            "kodosumi.service.expose.migrate_control."
            "request_previous_deregistration",
            new_callable=AsyncMock,
            return_value={
                "migrationState": "Polling",
                "migrationError": "Stored identity does not match.",
            },
        ),
    ):
        with pytest.raises(ClientException) as error:
            await DEREGISTER_PREVIOUS(
                None,
                name="expose",
                data={"flow_url": "/flow"},
                state=_state(),
            )

    assert error.value.status_code == 502
    assert "does not match" in error.value.detail


@pytest.mark.asyncio
async def test_poll_does_not_restore_an_identity_cleared_by_another_poll():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    intent = _registration_meta(
        agentIdentifier="old-agent",
        registrationId="old-reg",
        deregistrationState="DeregistrationIntent",
    )
    cleared = _registration_meta()
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            side_effect=[intent, intent],
        ),
        patch(
            "kodosumi.service.expose.registry_control.advance_migration",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "kodosumi.service.expose.registry_control."
            "resume_active_deregistration",
            new_callable=AsyncMock,
            return_value=(row, cleared, {
                "id": "old-reg",
                "agentIdentifier": "old-agent",
                "state": "RegistrationConfirmed",
            }),
        ),
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "old-reg",
                "agentIdentifier": "old-agent",
                "state": "RegistrationConfirmed",
            },
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
        ) as write,
    ):
        result = await POLL(
            None,
            name="expose",
            data={"flow_url": "/flow"},
            state=_state(),
        )

    assert result["state"] == "NotRegistered"
    write.assert_not_called()


@pytest.mark.asyncio
async def test_poll_discards_a_result_for_a_replaced_identity():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    original = _registration_meta(
        agentIdentifier="old-agent",
        registrationId="old-reg",
        deregistrationState="DeregistrationIntent",
    )
    replacement = _registration_meta(
        agentIdentifier="new-agent",
        registrationId="new-reg",
    )
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            side_effect=[original, original],
        ),
        patch(
            "kodosumi.service.expose.registry_control.advance_migration",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "kodosumi.service.expose.registry_control."
            "resume_active_deregistration",
            new_callable=AsyncMock,
            return_value=(row, replacement, {
                "id": "old-reg",
                "agentIdentifier": "old-agent",
                "state": "RegistrationConfirmed",
            }),
        ),
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "id": "old-reg",
                "agentIdentifier": "old-agent",
                "state": "RegistrationConfirmed",
            },
        ),
        patch(
            "kodosumi.service.expose.registry_control.update_flow_meta",
            new_callable=AsyncMock,
        ) as write,
    ):
        result = await POLL(
            None,
            name="expose",
            data={"flow_url": "/flow"},
            state=_state(),
        )

    assert result == {"state": "Polling", "registrationId": "new-reg"}
    write.assert_not_called()


@pytest.mark.asyncio
async def test_stale_cancel_does_not_clear_a_newer_migration():
    initial = {
        "name": "expose", "network": "Preprod", "meta": "[]",
        "updated": 10.0,
    }
    fresh = {**initial, "updated": 11.0}
    with (
        patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            side_effect=[initial, fresh],
        ),
        patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            return_value=_registration_meta(
                agentIdentifier="agent",
                pendingMigration={"registrationId": "new-reg"},
            ),
        ),
        patch(
            "kodosumi.service.expose.migrate_control.update_flow_meta",
            new_callable=AsyncMock,
        ) as write,
    ):
        with pytest.raises(ClientException) as error:
            await CANCEL(
                None,
                name="expose",
                data={"flow_url": "/flow", "meta_etag": "10.0"},
                state=_state(),
            )

    assert error.value.status_code == 409
    write.assert_not_called()


@pytest.mark.asyncio
async def test_stale_active_deregister_does_not_burn_a_new_agent():
    initial = {
        "name": "expose", "network": "Preprod", "meta": "[]",
        "updated": 10.0,
    }
    fresh = {**initial, "updated": 11.0}
    with (
        patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock,
            side_effect=[initial, fresh],
        ),
        patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            side_effect=[
                _registration_meta(
                    agentIdentifier="old-agent", registrationId="old-reg"),
                _registration_meta(
                    agentIdentifier="new-agent", registrationId="new-reg"),
            ],
        ),
        patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        with pytest.raises(ClientException) as error:
            await DEREGISTER(
                None,
                name="expose",
                data={"flow_url": "/flow", "meta_etag": "10.0"},
                state=_state(),
            )

    assert error.value.status_code == 409
    deregister.assert_not_called()


@pytest.mark.asyncio
async def test_stale_previous_deregister_does_not_burn_a_new_target():
    initial = {
        "name": "expose", "network": "Preprod", "meta": "[]",
        "updated": 10.0,
    }
    fresh = {**initial, "updated": 11.0}
    with (
        patch(
            "kodosumi.service.expose.migrate_control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.db.get_expose",
            new_callable=AsyncMock,
            return_value=initial,
        ),
        patch(
            "kodosumi.service.expose.migrate_control.get_flow_meta",
            return_value=_v2_with_previous(),
        ),
        patch(
            "kodosumi.service.expose.migration.db.get_expose",
            new_callable=AsyncMock,
            return_value=fresh,
        ),
        patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock,
        ) as status,
        patch(
            "kodosumi.service.expose.migration.deregister_agent",
            new_callable=AsyncMock,
        ) as deregister,
    ):
        with pytest.raises(ClientException) as error:
            await DEREGISTER_PREVIOUS(
                None,
                name="expose",
                data={"flow_url": "/flow", "meta_etag": "10.0"},
                state=_state(),
            )

    assert error.value.status_code == 409
    status.assert_not_called()
    deregister.assert_not_called()
