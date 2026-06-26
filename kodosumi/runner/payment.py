"""
Masumi payment integration for Kodosumi.

Handles payment initialization, status polling, and result submission
for flows with agentIdentifier configured in their meta.data.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple
from kodosumi.const import EVENT_DEBUG
import httpx

from kodosumi.config import MasumiConfig
from kodosumi.log import logger, slog

# OD-7: Any concrete non-FundsLocked on-chain state is treated as terminal for
# a job still in the payment-wait phase.  These are the known states as of the
# current Masumi API version.  Unknown states (Masumi API evolution) are NOT
# listed here — the loop continues polling until the deadline before raising
# PaymentDeadlineTimeoutError, protecting against transient/intermediate states
# introduced by future Masumi releases.
KNOWN_TERMINAL_STATES: frozenset = frozenset({
    "FundsOrDatumInvalid",
    "RefundRequested",
    "RefundWithdrawn",
    "Withdrawn",
    "Disputed",
    "ResultSubmitted",
})


class PaymentError(Exception):
    """Base exception for payment-related errors."""
    pass


class PaymentTimeoutError(PaymentError):
    """Payment was not confirmed within the deadline."""
    pass


class BuyerNoActionTimeoutError(PaymentTimeoutError):
    """Deadline expired and no on-chain state was ever observed.

    The buyer never initiated the on-chain transaction within the payment
    window.  This is distinct from a terminal failure state — the blockchain
    simply has no record of the payment.

    Attributes:
        blockchain_identifier: The blockchain payment identifier.
        network: Masumi network name ("Preprod" or "Mainnet").
        agent_identifier: The registered agent identifier on Masumi.
        deadline_iso: ISO-formatted deadline that was exceeded.
    """

    def __init__(
        self,
        blockchain_identifier: str,
        network: str,
        agent_identifier: str,
        deadline_iso: str,
    ) -> None:
        self.blockchain_identifier = blockchain_identifier
        self.network = network
        self.agent_identifier = agent_identifier
        self.deadline_iso = deadline_iso
        super().__init__(
            f"Payment deadline expired with no on-chain activity — "
            f"buyer never acted "
            f"(blockchain_id={blockchain_identifier}, network={network}, "
            f"agent={agent_identifier}, deadline={deadline_iso})"
        )


class PaymentRejectedError(PaymentTimeoutError):
    """A known terminal on-chain state was observed before the deadline.

    Raised immediately when a concrete non-FundsLocked terminal state is
    seen (e.g. "RefundRequested", "Disputed").  Continuing to poll would be
    wasteful — the state is deterministic.

    Attributes:
        blockchain_identifier: The blockchain payment identifier.
        network: Masumi network name.
        agent_identifier: The registered agent identifier.
        on_chain_state: The terminal on-chain state that was observed.
    """

    def __init__(
        self,
        blockchain_identifier: str,
        network: str,
        agent_identifier: str,
        on_chain_state: str,
    ) -> None:
        self.blockchain_identifier = blockchain_identifier
        self.network = network
        self.agent_identifier = agent_identifier
        self.on_chain_state = on_chain_state
        super().__init__(
            f"Payment reached terminal state '{on_chain_state}' — cannot proceed "
            f"(blockchain_id={blockchain_identifier}, network={network}, "
            f"agent={agent_identifier})"
        )


class PaymentDeadlineTimeoutError(PaymentTimeoutError):
    """Deadline expired after observing an unknown non-FundsLocked state.

    Raised when the deadline passes and the last observed on-chain state was
    not FundsLocked but also not in KNOWN_TERMINAL_STATES — i.e. Masumi may
    have introduced a new intermediate state.  Carries the last-seen state for
    diagnostics.

    Attributes:
        blockchain_identifier: The blockchain payment identifier.
        network: Masumi network name.
        agent_identifier: The registered agent identifier.
        last_state: The last on-chain state seen (or None if state was present
            but value was unexpected).
    """

    def __init__(
        self,
        blockchain_identifier: str,
        network: str,
        agent_identifier: str,
        last_state: Optional[str],
    ) -> None:
        self.blockchain_identifier = blockchain_identifier
        self.network = network
        self.agent_identifier = agent_identifier
        self.last_state = last_state
        super().__init__(
            f"Payment deadline expired with last on-chain state '{last_state}' — "
            f"payment not confirmed "
            f"(blockchain_id={blockchain_identifier}, network={network}, "
            f"agent={agent_identifier})"
        )


class PaymentInitError(PaymentError):
    """Failed to initialize payment request."""
    pass


class PaymentSubmitError(PaymentError):
    """Failed to submit result to Masumi."""
    pass


class MasumiClient:
    """
    Client for Masumi payment network API.

    Handles:
    - Payment initialization (/payment/)
    - Status polling for FundsLocked confirmation
    - Result submission (/payment/submit-result)
    """

    def __init__(self, config: MasumiConfig):
        self.base_url = config.base_url.rstrip("/")
        self.token = config.token
        self.pay_by_seconds = config.pay_by_time
        self.submit_result_seconds = config.submit_result_by_time
        self.poll_interval = config.poll_interval

    def _get_headers(self) -> dict:
        """Get headers for Masumi API requests."""
        return {
            "accept": "application/json",
            "token": self.token or "",
            "Content-Type": "application/json"
        }

    def _calculate_deadlines(self) -> Tuple[str, str]:
        """
        Calculate payment deadlines as ISO format strings.

        Returns:
            Tuple of (pay_by_time, submit_result_time) in ISO format
        """
        now = datetime.now(timezone.utc)
        pay_by = now.timestamp() + self.pay_by_seconds
        submit_by = now.timestamp() + self.submit_result_seconds

        # Convert to ISO format with milliseconds
        pay_by_iso = datetime.fromtimestamp(pay_by, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        submit_by_iso = datetime.fromtimestamp(submit_by, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )

        return pay_by_iso, submit_by_iso

    async def init_payment(
        self,
        agent_identifier: str,
        network: str,
        input_hash: str,
        identifier_from_purchaser: str,
        metadata: Optional[str] = None,
    ) -> dict:
        """
        Initialize a payment request with Masumi.

        Args:
            agent_identifier: The registered agent identifier on Masumi
            network: "Preprod" or "Mainnet"
            input_hash: Hash of the job inputs
            identifier_from_purchaser: Customer-provided identifier
            metadata: Optional private metadata for the payment

        Returns:
            Dict with payment details including blockchainIdentifier

        Raises:
            PaymentInitError: If the payment initialization fails
        """
        pay_by_time, submit_result_time = self._calculate_deadlines()

        payload = {
            "agentIdentifier": agent_identifier,
            "network": network,
            "inputHash": input_hash,
            "payByTime": pay_by_time,
            "submitResultTime": submit_result_time,
            "identifierFromPurchaser": identifier_from_purchaser,
        }

        # from kodosumi.helper import debug
        # debug()

        if metadata:
            payload["metadata"] = metadata

        # await self._put_async(EVENT_DEBUG, f"start request: {payload}")

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/payment/",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=30.0
                )
                # await self._put_async(EVENT_DEBUG, f"response: {resp.text}")
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                raise PaymentInitError(
                    f"Payment init failed with status {e.response.status_code}: "
                    f"{e.response.text}"
                )
            except httpx.RequestError as e:
                raise PaymentInitError(f"Payment init request failed: {e}")

    async def get_payment_status(
        self,
        blockchain_identifier: str,
        network: str,
    ) -> Optional[dict]:
        """
        Get the status of a payment by blockchain identifier.

        Uses POST /payment/resolve-blockchain-identifier for direct O(1) lookup
        instead of paginating through GET /payment.

        Args:
            blockchain_identifier: The blockchain identifier from init_payment
            network: "Preprod" or "Mainnet"

        Returns:
            Payment record dict if found, None otherwise
        """
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/payment/resolve-blockchain-identifier",
                    headers=self._get_headers(),
                    json={
                        "blockchainIdentifier": blockchain_identifier,
                        "network": network,
                    },
                    timeout=30.0
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json().get("data")

            except (httpx.HTTPStatusError, httpx.RequestError):
                slog(logger, logging.WARNING, "payment.status_error",
                     exc_info=True)
                return None

    async def wait_for_funds_locked(
        self,
        blockchain_identifier: str,
        network: str,
        pay_by_time: str,
        agent_identifier: str = "",
        on_event: Optional[Callable] = None,
    ) -> dict:
        """
        Poll for payment status until FundsLocked or timeout.

        Args:
            blockchain_identifier: The blockchain identifier from init_payment
            network: "Preprod" or "Mainnet"
            pay_by_time: Payment deadline as epoch milliseconds (string).
                         Note: Masumi payByTime is epoch-ms, not ISO — despite
                         the ISO-looking format of the init_payment request body.
            agent_identifier: The registered agent identifier (for diagnostics).
            on_event: Optional async callable invoked before raising, receives
                      a dict with structured payment-event fields.  Used by
                      Runner to persist an EVENT_PAYMENT row before the error
                      propagates.  Errors from on_event are swallowed.

        Returns:
            Payment record with FundsLocked status

        Raises:
            BuyerNoActionTimeoutError: Deadline expired, no on-chain state seen.
            PaymentRejectedError: A KNOWN_TERMINAL_STATES state was observed.
            PaymentDeadlineTimeoutError: Deadline expired with an unknown
                non-FundsLocked last state (Masumi API evolution guard).
        """
        # Parse deadline — payByTime is stored as epoch milliseconds
        deadline = datetime.fromtimestamp(float(pay_by_time) / 1000, timezone.utc)
        deadline_iso = deadline.isoformat()

        # Track the last non-None on-chain state seen across poll iterations.
        last_state: Optional[str] = None

        while datetime.now(timezone.utc) < deadline:
            payment = await self.get_payment_status(blockchain_identifier, network)

            if payment:
                state: Optional[str] = payment.get("onChainState")
                if state is not None:
                    last_state = state

                if state == "FundsLocked":
                    return payment

                # OD-7: Any KNOWN_TERMINAL_STATES state is deterministic —
                # raise immediately rather than waiting until deadline.
                # Unknown states: keep polling (guard against transient states
                # from future Masumi API versions).
                if state in KNOWN_TERMINAL_STATES:
                    slog(
                        logger,
                        logging.WARNING,
                        "payment.terminal_state",
                        blockchain_identifier=blockchain_identifier,
                        network=network,
                        agent=agent_identifier,
                        on_chain_state=state,
                    )
                    exc = PaymentRejectedError(
                        blockchain_identifier=blockchain_identifier,
                        network=network,
                        agent_identifier=agent_identifier,
                        on_chain_state=state,
                    )
                    if on_event is not None:
                        try:
                            await on_event({
                                "step": "terminal_state",
                                "reason": "rejected",
                                "blockchainIdentifier": blockchain_identifier,
                                "network": network,
                                "agent": agent_identifier,
                                "onChainState": state,
                            })
                        except Exception:
                            pass
                    raise exc

            await asyncio.sleep(self.poll_interval)

        # Deadline expired — distinguish "buyer never acted" from "unknown state"
        if last_state is None:
            slog(
                logger,
                logging.WARNING,
                "payment.buyer_no_action",
                blockchain_identifier=blockchain_identifier,
                network=network,
                agent=agent_identifier,
                deadline=deadline_iso,
            )
            exc = BuyerNoActionTimeoutError(
                blockchain_identifier=blockchain_identifier,
                network=network,
                agent_identifier=agent_identifier,
                deadline_iso=deadline_iso,
            )
            if on_event is not None:
                try:
                    await on_event({
                        "step": "timeout",
                        "reason": "buyer_no_action",
                        "blockchainIdentifier": blockchain_identifier,
                        "network": network,
                        "agent": agent_identifier,
                        "onChainState": None,
                        "deadline": deadline_iso,
                    })
                except Exception:
                    pass
            raise exc

        # Deadline expired with an unknown non-FundsLocked state — Masumi API
        # may have introduced a new intermediate state.
        slog(
            logger,
            logging.WARNING,
            "payment.deadline_timeout",
            blockchain_identifier=blockchain_identifier,
            network=network,
            agent=agent_identifier,
            last_state=last_state,
            deadline=deadline_iso,
        )
        exc = PaymentDeadlineTimeoutError(
            blockchain_identifier=blockchain_identifier,
            network=network,
            agent_identifier=agent_identifier,
            last_state=last_state,
        )
        if on_event is not None:
            try:
                await on_event({
                    "step": "timeout",
                    "reason": "deadline_with_unknown_state",
                    "blockchainIdentifier": blockchain_identifier,
                    "network": network,
                    "agent": agent_identifier,
                    "onChainState": last_state,
                    "deadline": deadline_iso,
                })
            except Exception:
                pass
        raise exc

    async def submit_result(
        self,
        blockchain_identifier: str,
        network: str,
        result_hash: str,
    ) -> dict:
        """
        Submit the job result hash to Masumi.

        Args:
            blockchain_identifier: The blockchain identifier from init_payment
            network: "Preprod" or "Mainnet"
            result_hash: Hash of the job result

        Returns:
            Response from Masumi

        Raises:
            PaymentSubmitError: If result submission fails
        """
        payload = {
            "blockchainIdentifier": blockchain_identifier,
            "network": network,
            "submitResultHash": result_hash,
        }

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/payment/submit-result",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=30.0
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                raise PaymentSubmitError(
                    f"Result submission failed with status {e.response.status_code}: "
                    f"{e.response.text}"
                )
            except httpx.RequestError as e:
                raise PaymentSubmitError(f"Result submission request failed: {e}")


def create_result_hash(result: Any) -> str:
    """
    Create a hash of the job result for submission.

    Uses the same hashing approach as input_hash.
    """
    import hashlib
    import json

    if result is None:
        data = ""
    elif isinstance(result, str):
        data = result
    else:
        try:
            data = json.dumps(result, sort_keys=True, default=str)
        except (TypeError, ValueError):
            data = str(result)

    return hashlib.sha256(data.encode()).hexdigest()
