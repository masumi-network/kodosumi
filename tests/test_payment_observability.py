"""
Unit tests for D13-paymentobs: payment timeout observability.

Tests cover:
- Exception class hierarchy and structured fields
- wait_for_funds_locked: FundsLocked success path
- wait_for_funds_locked: immediate raise on KNOWN_TERMINAL_STATES
- wait_for_funds_locked: deadline-expired with no on-chain state (buyer_no_action)
- wait_for_funds_locked: deadline-expired with unknown non-terminal state
- on_event callback is called with correct fields
- on_event=None does not raise
- KNOWN_TERMINAL_STATES coverage
- slog warning emitted at correct points
"""

import asyncio
import logging
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kodosumi.runner.payment import (
    KNOWN_TERMINAL_STATES,
    BuyerNoActionTimeoutError,
    MasumiClient,
    PaymentDeadlineTimeoutError,
    PaymentRejectedError,
    PaymentTimeoutError,
)
from kodosumi.config import MasumiConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(poll_interval: float = 0.01) -> MasumiClient:
    """Return a MasumiClient with a fast poll interval for unit tests."""
    cfg = MasumiConfig(
        network="Preprod",
        base_url="https://test.masumi.network/api/v1",
        token="test-token",
        poll_interval=poll_interval,
    )
    return MasumiClient(cfg)


def _deadline_ms(offset_seconds: float) -> str:
    """Return an epoch-ms string *offset_seconds* from now."""
    return str(int((time.time() + offset_seconds) * 1000))


def _past_deadline_ms(offset_seconds: float = 2.0) -> str:
    """Return an epoch-ms string in the past."""
    return str(int((time.time() - offset_seconds) * 1000))


# ---------------------------------------------------------------------------
# Exception class hierarchy and structured attributes
# ---------------------------------------------------------------------------

class TestExceptionHierarchy:
    def test_buyer_no_action_is_payment_timeout_error(self):
        exc = BuyerNoActionTimeoutError(
            blockchain_identifier="bc123",
            network="Preprod",
            agent_identifier="agent:abc",
            deadline_iso="2026-01-01T00:00:00+00:00",
        )
        assert isinstance(exc, PaymentTimeoutError)
        assert exc.blockchain_identifier == "bc123"
        assert exc.network == "Preprod"
        assert exc.agent_identifier == "agent:abc"
        assert exc.deadline_iso == "2026-01-01T00:00:00+00:00"

    def test_payment_rejected_error_is_payment_timeout_error(self):
        exc = PaymentRejectedError(
            blockchain_identifier="bc456",
            network="Mainnet",
            agent_identifier="agent:xyz",
            on_chain_state="RefundRequested",
        )
        assert isinstance(exc, PaymentTimeoutError)
        assert exc.blockchain_identifier == "bc456"
        assert exc.network == "Mainnet"
        assert exc.on_chain_state == "RefundRequested"

    def test_deadline_timeout_error_is_payment_timeout_error(self):
        exc = PaymentDeadlineTimeoutError(
            blockchain_identifier="bc789",
            network="Preprod",
            agent_identifier="agent:def",
            last_state="SomeNewState",
        )
        assert isinstance(exc, PaymentTimeoutError)
        assert exc.last_state == "SomeNewState"

    def test_buyer_no_action_message_contains_coordinates(self):
        exc = BuyerNoActionTimeoutError("bc-1", "Preprod", "agent-1", "2026-01-01T00:00:00+00:00")
        msg = str(exc)
        assert "bc-1" in msg
        assert "Preprod" in msg
        assert "agent-1" in msg

    def test_payment_rejected_message_contains_state(self):
        exc = PaymentRejectedError("bc-2", "Preprod", "agent-2", "RefundRequested")
        assert "RefundRequested" in str(exc)
        assert "bc-2" in str(exc)

    def test_deadline_timeout_message_contains_last_state(self):
        exc = PaymentDeadlineTimeoutError("bc-3", "Mainnet", "agent-3", "NewUnknownState")
        assert "NewUnknownState" in str(exc)

    def test_agent_identifier_in_exception_message(self):
        exc = BuyerNoActionTimeoutError("bc-x", "Preprod", "agent:abc", "2026-01-01T00:00:00+00:00")
        assert "agent:abc" in str(exc)


# ---------------------------------------------------------------------------
# KNOWN_TERMINAL_STATES coverage
# ---------------------------------------------------------------------------

class TestKnownTerminalStates:
    def test_contains_expected_states(self):
        expected = {
            "FundsOrDatumInvalid",
            "RefundRequested",
            "RefundWithdrawn",
            "Withdrawn",
            "Disputed",
            "ResultSubmitted",
        }
        assert expected.issubset(KNOWN_TERMINAL_STATES)

    def test_funds_locked_not_in_terminal_states(self):
        assert "FundsLocked" not in KNOWN_TERMINAL_STATES

    def test_is_frozenset(self):
        assert isinstance(KNOWN_TERMINAL_STATES, frozenset)


# ---------------------------------------------------------------------------
# wait_for_funds_locked — happy path
# ---------------------------------------------------------------------------

class TestWaitForFundsLockedSuccess:
    @pytest.mark.asyncio
    async def test_funds_locked_returns_payment(self):
        """FundsLocked response returns the payment dict without raising."""
        client = _make_client()
        payment_record = {"onChainState": "FundsLocked", "blockchainIdentifier": "bc-ok"}
        client.get_payment_status = AsyncMock(return_value=payment_record)

        result = await client.wait_for_funds_locked(
            blockchain_identifier="bc-ok",
            network="Preprod",
            pay_by_time=_deadline_ms(30),
            agent_identifier="agent:test",
        )

        assert result["onChainState"] == "FundsLocked"

    @pytest.mark.asyncio
    async def test_funds_locked_after_initial_none(self):
        """Loop should keep polling if status is None, then return on FundsLocked."""
        client = _make_client()
        payment_record = {"onChainState": "FundsLocked"}
        client.get_payment_status = AsyncMock(side_effect=[None, None, payment_record])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.wait_for_funds_locked(
                blockchain_identifier="bc-ok2",
                network="Preprod",
                pay_by_time=_deadline_ms(30),
            )
        assert result["onChainState"] == "FundsLocked"


# ---------------------------------------------------------------------------
# wait_for_funds_locked — terminal state path (PaymentRejectedError)
# ---------------------------------------------------------------------------

class TestWaitForFundsLockedTerminalState:
    @pytest.mark.asyncio
    async def test_known_terminal_state_raises_immediately(self):
        """A KNOWN_TERMINAL_STATES state raises PaymentRejectedError before deadline."""
        client = _make_client()
        client.get_payment_status = AsyncMock(
            return_value={"onChainState": "RefundRequested"}
        )

        with pytest.raises(PaymentRejectedError) as exc_info:
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-ref",
                network="Preprod",
                pay_by_time=_deadline_ms(60),  # deadline in the future
                agent_identifier="agent:ref",
            )

        exc = exc_info.value
        assert exc.on_chain_state == "RefundRequested"
        assert exc.blockchain_identifier == "bc-ref"
        assert exc.network == "Preprod"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", list(KNOWN_TERMINAL_STATES))
    async def test_all_known_terminal_states_raise_rejected(self, state: str):
        """Every state in KNOWN_TERMINAL_STATES must raise PaymentRejectedError."""
        client = _make_client()
        client.get_payment_status = AsyncMock(return_value={"onChainState": state})

        with pytest.raises(PaymentRejectedError) as exc_info:
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-ts",
                network="Preprod",
                pay_by_time=_deadline_ms(60),
            )
        assert exc_info.value.on_chain_state == state


# ---------------------------------------------------------------------------
# wait_for_funds_locked — buyer_no_action path (BuyerNoActionTimeoutError)
# ---------------------------------------------------------------------------

class TestWaitForFundsLockedBuyerNoAction:
    @pytest.mark.asyncio
    async def test_deadline_no_state_raises_buyer_no_action(self):
        """Deadline expired with no on-chain state → BuyerNoActionTimeoutError."""
        client = _make_client()
        client.get_payment_status = AsyncMock(return_value=None)

        with pytest.raises(BuyerNoActionTimeoutError) as exc_info:
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-noop",
                network="Preprod",
                pay_by_time=_past_deadline_ms(),
                agent_identifier="agent:noop",
            )

        exc = exc_info.value
        assert exc.blockchain_identifier == "bc-noop"
        assert exc.network == "Preprod"
        assert exc.agent_identifier == "agent:noop"
        assert exc.deadline_iso  # non-empty

    @pytest.mark.asyncio
    async def test_deadline_state_none_in_payload_raises_buyer_no_action(self):
        """Deadline with payment record but onChainState=None → BuyerNoActionTimeoutError."""
        client = _make_client()
        # Payment record exists but state is None (not yet on-chain)
        client.get_payment_status = AsyncMock(return_value={"onChainState": None})

        with pytest.raises(BuyerNoActionTimeoutError):
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-nonstate",
                network="Preprod",
                pay_by_time=_past_deadline_ms(),
            )

    @pytest.mark.asyncio
    async def test_buyer_no_action_is_subclass_of_payment_timeout_error(self):
        """BuyerNoActionTimeoutError must be catchable as PaymentTimeoutError."""
        client = _make_client()
        client.get_payment_status = AsyncMock(return_value=None)

        with pytest.raises(PaymentTimeoutError):
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-compat",
                network="Preprod",
                pay_by_time=_past_deadline_ms(),
            )


# ---------------------------------------------------------------------------
# wait_for_funds_locked — deadline with unknown state (PaymentDeadlineTimeoutError)
# ---------------------------------------------------------------------------

class TestWaitForFundsLockedDeadlineUnknownState:
    @pytest.mark.asyncio
    async def test_deadline_unknown_state_raises_deadline_timeout(self):
        """Deadline with an unknown non-FundsLocked state → PaymentDeadlineTimeoutError.

        We need the loop to execute at least once so get_payment_status is called
        and last_state is set before the deadline expires.  We do this by setting a
        very short deadline and mocking asyncio.sleep so time advances past it after
        the first poll iteration.
        """
        client = _make_client(poll_interval=0.001)
        unknown_state = "SomeNewMasumiState"

        call_count = 0

        async def mock_get_status(_bid, _net):
            nonlocal call_count
            call_count += 1
            return {"onChainState": unknown_state}

        client.get_payment_status = mock_get_status

        # Deadline ~50 ms from now — long enough for one poll, then expires.
        deadline_ms = _deadline_ms(0.05)

        # Let asyncio.sleep actually sleep a tiny bit so the deadline passes.
        with pytest.raises(PaymentDeadlineTimeoutError) as exc_info:
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-unk",
                network="Mainnet",
                pay_by_time=deadline_ms,
            )

        exc = exc_info.value
        assert exc.last_state == unknown_state
        assert exc.blockchain_identifier == "bc-unk"
        assert call_count >= 1


# ---------------------------------------------------------------------------
# on_event callback
# ---------------------------------------------------------------------------

class TestOnEventCallback:
    @pytest.mark.asyncio
    async def test_on_event_called_on_buyer_no_action(self):
        """on_event AsyncMock receives correct fields for buyer_no_action."""
        client = _make_client()
        client.get_payment_status = AsyncMock(return_value=None)
        on_event = AsyncMock()

        with pytest.raises(BuyerNoActionTimeoutError):
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-cb-noop",
                network="Preprod",
                pay_by_time=_past_deadline_ms(),
                agent_identifier="agent:cb",
                on_event=on_event,
            )

        on_event.assert_called_once()
        payload = on_event.call_args[0][0]
        assert payload["reason"] == "buyer_no_action"
        assert payload["step"] == "timeout"
        assert payload["blockchainIdentifier"] == "bc-cb-noop"
        assert payload["network"] == "Preprod"
        assert payload["agent"] == "agent:cb"
        assert payload["onChainState"] is None

    @pytest.mark.asyncio
    async def test_on_event_called_on_rejection(self):
        """on_event receives correct fields when a terminal state is observed."""
        client = _make_client()
        client.get_payment_status = AsyncMock(
            return_value={"onChainState": "RefundRequested"}
        )
        on_event = AsyncMock()

        with pytest.raises(PaymentRejectedError):
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-cb-rej",
                network="Mainnet",
                pay_by_time=_deadline_ms(60),
                agent_identifier="agent:rej",
                on_event=on_event,
            )

        on_event.assert_called_once()
        payload = on_event.call_args[0][0]
        assert payload["reason"] == "rejected"
        assert payload["onChainState"] == "RefundRequested"
        assert payload["blockchainIdentifier"] == "bc-cb-rej"

    @pytest.mark.asyncio
    async def test_on_event_none_does_not_raise(self):
        """Omitting on_event (default None) must not cause AttributeError or crash."""
        client = _make_client()
        client.get_payment_status = AsyncMock(return_value=None)

        # Should raise BuyerNoActionTimeoutError only — no AttributeError from on_event
        with pytest.raises(BuyerNoActionTimeoutError):
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-nonecb",
                network="Preprod",
                pay_by_time=_past_deadline_ms(),
                # on_event omitted
            )

    @pytest.mark.asyncio
    async def test_on_event_exception_is_swallowed(self):
        """An on_event callback that raises must not prevent the payment error from propagating."""
        client = _make_client()
        client.get_payment_status = AsyncMock(return_value=None)

        async def bad_callback(_payload):
            raise RuntimeError("callback exploded")

        with pytest.raises(BuyerNoActionTimeoutError):
            await client.wait_for_funds_locked(
                blockchain_identifier="bc-bad-cb",
                network="Preprod",
                pay_by_time=_past_deadline_ms(),
                on_event=bad_callback,
            )


# ---------------------------------------------------------------------------
# Structured logging (slog) emitted on terminal outcomes
# ---------------------------------------------------------------------------

class TestStructuredLogging:
    @pytest.mark.asyncio
    async def test_slog_emitted_on_buyer_no_action(self, caplog):
        """payment.buyer_no_action WARNING must be emitted when deadline expires."""
        client = _make_client()
        client.get_payment_status = AsyncMock(return_value=None)

        with caplog.at_level(logging.WARNING, logger="kodo"):
            with pytest.raises(BuyerNoActionTimeoutError):
                await client.wait_for_funds_locked(
                    blockchain_identifier="bc-log-noop",
                    network="Preprod",
                    pay_by_time=_past_deadline_ms(),
                    agent_identifier="agent:log",
                )

        events = [r.getMessage() for r in caplog.records]
        assert any("payment.buyer_no_action" in e for e in events), (
            f"Expected 'payment.buyer_no_action' in log records, got: {events}"
        )

    @pytest.mark.asyncio
    async def test_slog_emitted_on_terminal_state(self, caplog):
        """payment.terminal_state WARNING must be emitted for known terminal states."""
        client = _make_client()
        client.get_payment_status = AsyncMock(
            return_value={"onChainState": "Disputed"}
        )

        with caplog.at_level(logging.WARNING, logger="kodo"):
            with pytest.raises(PaymentRejectedError):
                await client.wait_for_funds_locked(
                    blockchain_identifier="bc-log-term",
                    network="Mainnet",
                    pay_by_time=_deadline_ms(60),
                    agent_identifier="agent:term",
                )

        events = [r.getMessage() for r in caplog.records]
        assert any("payment.terminal_state" in e for e in events), (
            f"Expected 'payment.terminal_state' in log records, got: {events}"
        )

    @pytest.mark.asyncio
    async def test_slog_emitted_on_deadline_timeout(self, caplog):
        """payment.deadline_timeout WARNING must be emitted for unknown states.

        Uses the same short-deadline approach as the unit test above to ensure
        the loop body executes once and last_state is populated before the
        deadline expires.
        """
        client = _make_client(poll_interval=0.001)

        async def mock_get_status(_bid, _net):
            return {"onChainState": "UnknownFutureState"}

        client.get_payment_status = mock_get_status

        with caplog.at_level(logging.WARNING, logger="kodo"):
            with pytest.raises(PaymentDeadlineTimeoutError):
                await client.wait_for_funds_locked(
                    blockchain_identifier="bc-log-dl",
                    network="Preprod",
                    pay_by_time=_deadline_ms(0.05),
                )

        events = [r.getMessage() for r in caplog.records]
        assert any("payment.deadline_timeout" in e for e in events), (
            f"Expected 'payment.deadline_timeout' in log records, got: {events}"
        )
