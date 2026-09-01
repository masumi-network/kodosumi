"""
Tests for the V1 to V2 migration of a registered flow.

Masumi cannot upgrade a registration in place, so a migration mints a
second agent and swaps the flow over once that mint confirms. The V1 agent
must keep serving until then, and the old entry must stay reachable for a
later burn.
"""

import pytest
from unittest.mock import AsyncMock, patch

from kodosumi.config import MasumiConfig
from kodosumi.service.expose.migration import (
    advance_migration,
    confirmed_migration_updates,
    pending_migration,
    start_migration_updates,
)


def _make_config() -> MasumiConfig:
    return MasumiConfig(
        network="Preprod",
        base_url="https://test.masumi.network/api/v1",
        token="test-token",
        poll_interval=1.0,
    )


def _registered_meta() -> dict:
    return {
        "display": "My Agent",
        "agentIdentifier": "v1-agent",
        "registrationId": "v1-reg",
        "agentPricing": [{"pricingType": "Free"}],
    }


def _pending(deregister_previous=False) -> dict:
    return {
        "registrationId": "v2-reg",
        "paymentSourceType": "Web3CardanoV2",
        "supportedPaymentSourceIndex": 0,
        "deregisterPrevious": deregister_previous,
    }


class TestStartMigrationUpdates:
    """The V1 agent must keep answering jobs while the V2 mint runs."""

    def test_records_the_new_registration_as_pending(self):
        updates = start_migration_updates("v2-reg", deregister_previous=True)
        assert updates["pendingMigration"] == {
            "registrationId": "v2-reg",
            "paymentSourceType": "Web3CardanoV2",
            "supportedPaymentSourceIndex": 0,
            "deregisterPrevious": True,
        }

    def test_leaves_the_live_registration_alone(self):
        updates = start_migration_updates("v2-reg", deregister_previous=False)
        assert "agentIdentifier" not in updates
        assert "registrationId" not in updates
        assert "paymentSourceType" not in updates


class TestPendingMigration:

    def test_absent_and_broken_values_read_as_none(self):
        for value in ({}, {"pendingMigration": None},
                      {"pendingMigration": "yes"},
                      {"pendingMigration": {"paymentSourceType": "Web3CardanoV2"}}):
            assert pending_migration(value) is None

    def test_returns_the_pending_block(self):
        assert pending_migration({"pendingMigration": _pending()}) == _pending()


class TestConfirmedMigrationUpdates:

    def test_swaps_the_flow_onto_the_new_agent(self):
        updates = confirmed_migration_updates(
            _registered_meta(), _pending(), "v2-agent", keep_previous=True)
        assert updates["agentIdentifier"] == "v2-agent"
        assert updates["registrationId"] == "v2-reg"
        assert updates["paymentSourceType"] == "Web3CardanoV2"
        assert updates["supportedPaymentSourceIndex"] == 0
        assert updates["pendingMigration"] is None

    def test_records_the_old_agent_for_a_later_burn(self):
        updates = confirmed_migration_updates(
            _registered_meta(), _pending(), "v2-agent", keep_previous=True)
        assert updates["previousRegistration"] == {
            "agentIdentifier": "v1-agent",
            "registrationId": "v1-reg",
            "paymentSourceType": "Web3CardanoV1",
        }

    def test_burned_old_agent_is_not_recorded(self):
        updates = confirmed_migration_updates(
            _registered_meta(), _pending(), "v2-agent", keep_previous=False)
        assert updates["previousRegistration"] is None


class TestAdvanceMigration:

    def _patch(self, status_result, deregister=None):
        return (
            patch("kodosumi.service.expose.migration.get_registration_status",
                  new_callable=AsyncMock, return_value=status_result),
            patch("kodosumi.service.expose.migration.deregister_agent",
                  new_callable=AsyncMock,
                  **({"side_effect": deregister} if isinstance(deregister, Exception)
                     else {"return_value": deregister or {}})),
            patch("kodosumi.service.expose.migration.update_flow_meta",
                  new_callable=AsyncMock, return_value="display: My Agent\n"),
        )

    @pytest.mark.asyncio
    async def test_no_pending_migration_does_nothing(self):
        status, dereg, write = self._patch(None)
        with status as mock_status, dereg, write as mock_write:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", _registered_meta())
        assert result is None
        mock_status.assert_not_called()
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_unconfirmed_mint_leaves_the_flow_on_v1(self):
        meta = {**_registered_meta(), "pendingMigration": _pending()}
        status, dereg, write = self._patch(
            {"state": "RegistrationRequested", "agentIdentifier": None})
        with status, dereg, write as mock_write:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta)
        assert result == {"migrationState": "RegistrationRequested"}
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_registry_answer_reports_polling(self):
        meta = {**_registered_meta(), "pendingMigration": _pending()}
        status, dereg, write = self._patch(None)
        with status, dereg, write as mock_write:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta)
        assert result == {"migrationState": "Polling"}
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmed_mint_swaps_the_flow_over(self):
        meta = {**_registered_meta(), "pendingMigration": _pending()}
        status, dereg, write = self._patch(
            {"state": "RegistrationConfirmed", "agentIdentifier": "v2-agent"})
        with status, dereg as mock_dereg, write as mock_write:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta)
        assert result["migrationState"] == "MigrationConfirmed"
        assert result["agentIdentifier"] == "v2-agent"
        assert result["deregisterError"] is None
        mock_dereg.assert_not_called()
        updates = mock_write.call_args.args[3]
        assert updates["agentIdentifier"] == "v2-agent"
        assert updates["previousRegistration"]["agentIdentifier"] == "v1-agent"

    @pytest.mark.asyncio
    async def test_old_agent_is_burned_only_after_the_new_one_exists(self):
        meta = {**_registered_meta(),
                "pendingMigration": _pending(deregister_previous=True)}
        status, dereg, write = self._patch(
            {"state": "RegistrationConfirmed", "agentIdentifier": "v2-agent"})
        with status, dereg as mock_dereg, write as mock_write:
            await advance_migration(
                _make_config(), {}, "expose", "/flow", meta)
        assert mock_dereg.call_args.args[1] == "v1-agent"
        assert mock_write.call_args.args[3]["previousRegistration"] is None

    @pytest.mark.asyncio
    async def test_failed_burn_keeps_the_old_agent_reachable(self):
        meta = {**_registered_meta(),
                "pendingMigration": _pending(deregister_previous=True)}
        status, dereg, write = self._patch(
            {"state": "RegistrationConfirmed", "agentIdentifier": "v2-agent"},
            deregister=RuntimeError("Deregistration failed: no collateral"))
        with status, dereg, write as mock_write:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta)
        # The V2 agent is live either way, so the migration still completes.
        assert result["migrationState"] == "MigrationConfirmed"
        assert "no collateral" in result["deregisterError"]
        assert mock_write.call_args.args[3]["previousRegistration"][
            "agentIdentifier"] == "v1-agent"

    @pytest.mark.asyncio
    async def test_registry_error_does_not_lose_the_pending_migration(self):
        meta = {**_registered_meta(), "pendingMigration": _pending()}
        status = patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock, side_effect=RuntimeError("boom"))
        write = patch("kodosumi.service.expose.migration.update_flow_meta",
                      new_callable=AsyncMock)
        with status, write as mock_write:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta)
        assert result["migrationState"] == "Polling"
        assert "boom" in result["migrationError"]
        mock_write.assert_not_called()
