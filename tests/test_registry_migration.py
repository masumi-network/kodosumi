"""
Tests for the V1 to V2 migration of a registered flow.

Masumi cannot upgrade a registration in place, so a migration mints a
second agent and swaps the flow over once that mint confirms. The V1 agent
must keep serving until then, and the old entry must stay reachable for a
later burn.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from kodosumi.config import MasumiConfig
from kodosumi.service.expose.migration import (
    advance_migration,
    burn_target,
    cancel_migration_updates,
    confirmed_migration_updates,
    failed_migration_updates,
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


def _previous(deregister_requested=True) -> dict:
    return {
        "agentIdentifier": "v1-agent",
        "registrationId": "v1-reg",
        "paymentSourceType": "Web3CardanoV1",
        "deregisterRequested": deregister_requested,
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

    def test_a_retry_clears_the_error_of_the_last_attempt(self):
        updates = start_migration_updates("v2-reg", deregister_previous=False)
        assert updates["migrationError"] is None


class TestPendingMigration:

    def test_absent_and_broken_values_read_as_none(self):
        for value in ({}, {"pendingMigration": None},
                      {"pendingMigration": "yes"},
                      {"pendingMigration": {"paymentSourceType": "Web3CardanoV2"}}):
            assert pending_migration(value) is None

    def test_returns_the_pending_block(self):
        assert pending_migration({"pendingMigration": _pending()}) == _pending()


class TestBurnTarget:
    """The intent to burn has to outlive the pending record."""

    def test_requested_burn_is_returned(self):
        meta = {"previousRegistration": _previous(deregister_requested=True)}
        assert burn_target(meta) == _previous(deregister_requested=True)

    def test_a_kept_v1_agent_is_never_burned(self):
        meta = {"previousRegistration": _previous(deregister_requested=False)}
        assert burn_target(meta) is None

    def test_absent_and_broken_values_read_as_none(self):
        for value in ({}, {"previousRegistration": None},
                      {"previousRegistration": "v1-agent"},
                      {"previousRegistration": {"deregisterRequested": True}}):
            assert burn_target(value) is None


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
        assert updates["previousRegistration"] == _previous(
            deregister_requested=False)

    def test_a_requested_burn_survives_the_pending_record(self):
        updates = confirmed_migration_updates(
            _registered_meta(), _pending(deregister_previous=True),
            "v2-agent", keep_previous=True)
        assert updates["previousRegistration"]["deregisterRequested"] is True

    def test_burned_old_agent_is_not_recorded(self):
        updates = confirmed_migration_updates(
            _registered_meta(), _pending(), "v2-agent", keep_previous=False)
        assert updates["previousRegistration"] is None


class TestFailedAndCancelledMigration:
    """A migration that stops must not leave the flow wedged."""

    def test_a_failed_mint_clears_the_pending_record(self):
        updates = failed_migration_updates("RegistrationFailed")
        assert updates["pendingMigration"] is None
        assert "RegistrationFailed" in updates["migrationError"]

    def test_a_cancelled_migration_clears_the_pending_record(self):
        updates = cancel_migration_updates()
        assert updates["pendingMigration"] is None
        assert "cancelled" in updates["migrationError"]


def _stateful_meta(meta: dict):
    """Model the stored flow metadata, so a re-read sees the last write.

    advance_migration re-reads between its steps on purpose: the burn has to
    act on what the swap actually wrote, not on the caller's stale copy.
    """
    stored = {"data": dict(meta)}

    async def fake_write(row, expose_name, flow_url, updates, base_data=None):
        for key, value in updates.items():
            if value is None:
                stored["data"].pop(key, None)
            else:
                stored["data"][key] = value
        return "display: My Agent\n"

    read_flow = patch(
        "kodosumi.service.expose.migration.get_flow_meta",
        side_effect=lambda row, flow_url: dict(stored["data"]))
    return stored, fake_write, read_flow


class TestAdvanceMigration:

    def _patch(self, status_result, deregister=None, row=None):
        return (
            patch("kodosumi.service.expose.migration.get_registration_status",
                  new_callable=AsyncMock, return_value=status_result),
            patch("kodosumi.service.expose.migration.deregister_agent",
                  new_callable=AsyncMock,
                  **({"side_effect": deregister} if isinstance(deregister, Exception)
                     else {"return_value": deregister or {}})),
            patch("kodosumi.service.expose.migration.update_flow_meta",
                  new_callable=AsyncMock, return_value="display: My Agent\n"),
            patch("kodosumi.service.expose.migration.db.get_expose",
                  new_callable=AsyncMock,
                  return_value=row if row is not None else {"meta": "[]"}),
        )

    @pytest.mark.asyncio
    async def test_no_pending_migration_does_nothing(self):
        status, dereg, write, read = self._patch(None)
        with status as mock_status, dereg, write as mock_write, read:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", _registered_meta())
        assert result is None
        mock_status.assert_not_called()
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_unconfirmed_mint_leaves_the_flow_on_v1(self):
        meta = {**_registered_meta(), "pendingMigration": _pending()}
        status, dereg, write, read = self._patch(
            {"state": "RegistrationRequested", "agentIdentifier": None})
        with status, dereg, write as mock_write, read:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta)
        assert result == {"migrationState": "RegistrationRequested"}
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_registry_answer_reports_polling(self):
        meta = {**_registered_meta(), "pendingMigration": _pending()}
        status, dereg, write, read = self._patch(None)
        with status, dereg, write as mock_write, read:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta)
        assert result == {"migrationState": "Polling"}
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmed_mint_swaps_the_flow_over(self):
        meta = {**_registered_meta(), "pendingMigration": _pending()}
        status, dereg, write, read = self._patch(
            {"state": "RegistrationConfirmed", "agentIdentifier": "v2-agent"})
        with status, dereg as mock_dereg, write as mock_write, read:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta, allow_burn=True)
        assert result["migrationState"] == "MigrationConfirmed"
        assert result["agentIdentifier"] == "v2-agent"
        mock_dereg.assert_not_called()
        updates = mock_write.call_args.args[3]
        assert updates["agentIdentifier"] == "v2-agent"
        assert updates["previousRegistration"]["agentIdentifier"] == "v1-agent"

    @pytest.mark.asyncio
    async def test_a_failed_mint_clears_the_pending_record(self):
        meta = {**_registered_meta(), "pendingMigration": _pending()}
        status, dereg, write, read = self._patch(
            {"state": "RegistrationFailed", "agentIdentifier": None})
        with status, dereg as mock_dereg, write as mock_write, read:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta, allow_burn=True)
        assert result["migrationState"] == "RegistrationFailed"
        # Without this write the flow refuses both a retry and a plain
        # deregistration for good.
        assert mock_write.call_args.args[3]["pendingMigration"] is None
        assert "RegistrationFailed" in result["migrationError"]
        mock_dereg.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_read_only_call_never_burns_the_old_agent(self):
        meta = {**_registered_meta(),
                "pendingMigration": _pending(deregister_previous=True)}
        status, dereg, write, read = self._patch(
            {"state": "RegistrationConfirmed", "agentIdentifier": "v2-agent"})
        with status, dereg as mock_dereg, write as mock_write, read:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta, allow_burn=False)
        assert result["migrationState"] == "MigrationConfirmed"
        # A GET must not deregister anything. The intent is recorded and the
        # next poll performs the burn.
        mock_dereg.assert_not_called()
        assert mock_write.call_args.args[3]["previousRegistration"][
            "deregisterRequested"] is True

    @pytest.mark.asyncio
    async def test_a_recorded_burn_runs_on_a_later_poll(self):
        meta = {**_registered_meta(),
                "agentIdentifier": "v2-agent",
                "paymentSourceType": "Web3CardanoV2",
                "previousRegistration": _previous()}
        status, dereg, write, read = self._patch(None)
        with status as mock_status, dereg as mock_dereg, write as mock_write, read:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta, allow_burn=True)
        # No mint is pending any more, so the registry is not asked again.
        mock_status.assert_not_called()
        assert mock_dereg.call_args.args[1] == "v1-agent"
        assert result["deregisteredPrevious"] == "v1-agent"
        assert mock_write.call_args.args[3] == {
            "previousRegistration": None, "migrationError": None}

    @pytest.mark.asyncio
    async def test_old_agent_is_burned_only_after_the_swap_is_written(self):
        meta = {**_registered_meta(),
                "pendingMigration": _pending(deregister_previous=True)}
        stored, fake_write, read_flow = _stateful_meta(meta)
        status, dereg, write, read = self._patch(
            {"state": "RegistrationConfirmed", "agentIdentifier": "v2-agent"})
        with status, dereg as mock_dereg, write as mock_write, read, read_flow:
            mock_write.side_effect = fake_write
            await advance_migration(
                _make_config(), {}, "expose", "/flow", meta, allow_burn=True)
        assert mock_dereg.call_args.args[1] == "v1-agent"
        # The swap lands first, so a burn is never lost to a failed write.
        first_updates = mock_write.call_args_list[0].args[3]
        assert first_updates["agentIdentifier"] == "v2-agent"
        assert first_updates["pendingMigration"] is None
        # The burned agent is dropped afterwards, off a re-read row.
        assert mock_write.call_args_list[-1].args[3] == {
            "previousRegistration": None, "migrationError": None}
        assert len(mock_write.call_args_list) == 2

    @pytest.mark.asyncio
    async def test_a_status_call_without_a_flow_url_changes_nothing(self):
        meta = {**_registered_meta(),
                "pendingMigration": _pending(deregister_previous=True)}
        status, dereg, write, read = self._patch(
            {"state": "RegistrationConfirmed", "agentIdentifier": "v2-agent"})
        with status as mock_status, dereg as mock_dereg, write as mock_write, read:
            result = await advance_migration(
                _make_config(), {}, "expose", "", meta, allow_burn=True)
        assert result is None
        mock_status.assert_not_called()
        mock_dereg.assert_not_called()
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_burn_keeps_the_old_agent_reachable(self):
        meta = {**_registered_meta(),
                "pendingMigration": _pending(deregister_previous=True)}
        stored, fake_write, read_flow = _stateful_meta(meta)
        status, dereg, write, read = self._patch(
            {"state": "RegistrationConfirmed", "agentIdentifier": "v2-agent"},
            deregister=RuntimeError("Deregistration failed: no collateral"))
        with status, dereg, write as mock_write, read, read_flow:
            mock_write.side_effect = fake_write
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta, allow_burn=True)
        # The V2 agent is live either way, so the migration still completes.
        assert result["migrationState"] == "MigrationConfirmed"
        assert "no collateral" in result["deregisterError"]
        last_updates = mock_write.call_args.args[3]
        # The recorded V1 agent survives the failed burn, and the reason is
        # written down so the next page load can still show it.
        assert last_updates["previousRegistration"][
            "agentIdentifier"] == "v1-agent"
        assert "no collateral" in last_updates["migrationError"]
        # The automatic retry is dropped: a node that keeps refusing must
        # not be called again on every poll.
        assert last_updates["previousRegistration"][
            "deregisterRequested"] is False

    @pytest.mark.asyncio
    async def test_registry_error_does_not_lose_the_pending_migration(self):
        meta = {**_registered_meta(), "pendingMigration": _pending()}
        status = patch(
            "kodosumi.service.expose.migration.get_registration_status",
            new_callable=AsyncMock, side_effect=RuntimeError("boom"))
        write = patch("kodosumi.service.expose.migration.update_flow_meta",
                      new_callable=AsyncMock)
        read = patch("kodosumi.service.expose.migration.db.get_expose",
                     new_callable=AsyncMock, return_value={"meta": "[]"})
        with status, write as mock_write, read:
            result = await advance_migration(
                _make_config(), {}, "expose", "/flow", meta)
        assert result["migrationState"] == "Polling"
        assert "boom" in result["migrationError"]
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_two_callers_swap_and_burn_once(self):
        """Two open tabs must not both deregister the same agent."""
        meta = {**_registered_meta(),
                "pendingMigration": _pending(deregister_previous=True)}
        stored, fake_write, read_flow = _stateful_meta(meta)
        status, dereg, write, read = self._patch(
            {"state": "RegistrationConfirmed", "agentIdentifier": "v2-agent"})
        with status, dereg as mock_dereg, write as mock_write, read, read_flow:
            mock_write.side_effect = fake_write
            results = await asyncio.gather(
                advance_migration(_make_config(), {}, "expose", "/flow",
                                  dict(meta), allow_burn=True),
                advance_migration(_make_config(), {}, "expose", "/flow",
                                  dict(meta), allow_burn=True),
            )
        assert mock_dereg.call_count == 1
        assert stored["data"]["agentIdentifier"] == "v2-agent"
        assert "previousRegistration" not in stored["data"]
        # The second caller finds nothing left to do.
        assert [r for r in results if r is None] == [None]
