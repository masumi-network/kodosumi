"""
Masumi payment integration for Kodosumi.

Handles payment initialization, status polling, and result submission
for flows with agentIdentifier configured in their meta.data.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional, Tuple
from kodosumi.const import EVENT_DEBUG
import httpx

from kodosumi.config import MasumiConfig

class PaymentError(Exception):
    """Base exception for payment-related errors."""
    pass


class PaymentTimeoutError(PaymentError):
    """Payment was not confirmed within the deadline."""
    pass


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

        Args:
            blockchain_identifier: The blockchain identifier from init_payment
            network: "Preprod" or "Mainnet"

        Returns:
            Payment record dict if found, None otherwise
        """
        async with httpx.AsyncClient() as client:
            try:
                cursor_id = None
                while True:
                    params = {
                        "limit": 100,
                        "network": network,
                        "includeHistory": "false"
                    }
                    if cursor_id:
                        params["cursorId"] = cursor_id

                    resp = await client.get(
                        f"{self.base_url}/payment/",
                        headers=self._get_headers(),
                        params=params,
                        timeout=30.0
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    payments = data.get("data", {}).get("Payments", [])
                    for payment in payments:
                        if payment.get("blockchainIdentifier") == blockchain_identifier:
                            return payment

                    # No more pages if fewer results than limit
                    if len(payments) < 100:
                        return None

                    # Paginate using last payment's ID
                    cursor_id = payments[-1].get("id")
                    if not cursor_id:
                        return None

            except (httpx.HTTPStatusError, httpx.RequestError):
                return None

    async def wait_for_funds_locked(
        self,
        blockchain_identifier: str,
        network: str,
        pay_by_time: str,
    ) -> dict:
        """
        Poll for payment status until FundsLocked or timeout.

        Args:
            blockchain_identifier: The blockchain identifier from init_payment
            network: "Preprod" or "Mainnet"
            pay_by_time: ISO format deadline for payment

        Returns:
            Payment record with FundsLocked status

        Raises:
            PaymentTimeoutError: If payment not confirmed by deadline
        """
        # Parse deadline
        deadline = datetime.fromtimestamp(float(pay_by_time)/1000, timezone.utc)


        while datetime.now(timezone.utc) < deadline:
            payment = await self.get_payment_status(blockchain_identifier, network)

            if payment:
                state = payment.get("onChainState", None)
                if state:
                    if state == "FundsLocked":
                        return payment
                    else:
                        raise PaymentTimeoutError(
                            f"Payment was {state}, cannot proceed with job"
                        )

            await asyncio.sleep(self.poll_interval)

        raise PaymentTimeoutError(
            f"Payment not confirmed by deadline {pay_by_time}"
        )

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
