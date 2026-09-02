"""Regression tests for PR 88 payment source type integrity."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kodosumi.config import MasumiConfig
from kodosumi.const import EVENT_PAYMENT
from kodosumi.runner.main import Runner
from kodosumi.runner.payment import MasumiClient


def _config() -> MasumiConfig:
    return MasumiConfig(
        network="Preprod",
        base_url="https://test.masumi.network/api/v1",
        token="test-token",
        poll_interval=1.0,
    )


def _response(data: dict):
    response = MagicMock()
    response.json.return_value = data
    response.raise_for_status = MagicMock()
    return response


@pytest.mark.asyncio
async def test_init_payment_sends_expected_payment_source_type():
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post.return_value = _response({"data": {}})

    with patch("httpx.AsyncClient", return_value=client):
        await MasumiClient(_config()).init_payment(
            agent_identifier="agent",
            network="Preprod",
            input_hash="input-hash",
            identifier_from_purchaser="purchaser",
            payment_source_type="Web3CardanoV2",
            supported_payment_source_index=0,
        )

    payload = client.post.call_args.kwargs["json"]
    assert payload["paymentSourceType"] == "Web3CardanoV2"


@pytest.mark.asyncio
async def test_existing_positional_source_index_keeps_its_position():
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post.return_value = _response({"data": {}})

    with patch("httpx.AsyncClient", return_value=client):
        await MasumiClient(_config()).init_payment(
            "agent", "Preprod", "input-hash", "purchaser", None, 0)

    payload = client.post.call_args.kwargs["json"]
    assert payload["supportedPaymentSourceIndex"] == 0
    assert "paymentSourceType" not in payload


@pytest.mark.asyncio
async def test_prepare_returns_payment_node_source_type():
    runner_class = Runner.__ray_metadata__.modified_class
    runner = object.__new__(runner_class)
    runner._payment_lock = asyncio.Lock()
    runner._payment = None
    runner.get_payment_config = AsyncMock(return_value={
        "agentIdentifier": "agent",
        "network": "Preprod",
        "identifier_from_purchaser": "purchaser",
        "input_hash": "input-hash",
        "paymentSourceType": "Web3CardanoV2",
        "supportedPaymentSourceIndex": 0,
    })
    runner._put_async = AsyncMock()

    payment_client = MagicMock()
    payment_client.init_payment = AsyncMock(return_value={
        "data": {
            "blockchainIdentifier": "chain-id",
            "PaymentSource": {
                "paymentSourceType": "Web3CardanoV1",
            },
        },
    })
    settings = SimpleNamespace(get_masumi=lambda _network: _config())

    with (
        patch("kodosumi.runner.main.Settings", return_value=settings),
        patch(
            "kodosumi.runner.main.MasumiClient",
            return_value=payment_client,
        ),
    ):
        result = await runner_class.prepare(runner)

    assert payment_client.init_payment.call_args.kwargs[
        "payment_source_type"
    ] == "Web3CardanoV2"
    assert result["pay_conf"]["paymentSourceType"] == "Web3CardanoV1"

    payment_call = next(
        call for call in runner._put_async.await_args_list
        if call.args[0] == EVENT_PAYMENT
    )
    event = json.loads(payment_call.args[1])["dict"]
    assert event["paymentSourceType"] == "Web3CardanoV1"


@pytest.mark.asyncio
async def test_prepare_defaults_legacy_payment_response_to_v1():
    runner_class = Runner.__ray_metadata__.modified_class
    runner = object.__new__(runner_class)
    runner._payment_lock = asyncio.Lock()
    runner._payment = None
    runner.get_payment_config = AsyncMock(return_value={
        "agentIdentifier": "agent",
        "network": "Preprod",
        "identifier_from_purchaser": "purchaser",
        "input_hash": "input-hash",
        "supportedPaymentSourceIndex": None,
    })
    runner._put_async = AsyncMock()

    payment_client = MagicMock()
    payment_client.init_payment = AsyncMock(return_value={
        "data": {"blockchainIdentifier": "chain-id"},
    })
    settings = SimpleNamespace(get_masumi=lambda _network: _config())

    with (
        patch("kodosumi.runner.main.Settings", return_value=settings),
        patch("kodosumi.runner.main.MasumiClient", return_value=payment_client),
    ):
        result = await runner_class.prepare(runner)

    assert result["pay_conf"]["paymentSourceType"] == "Web3CardanoV1"
    payment_call = next(
        call for call in runner._put_async.await_args_list
        if call.args[0] == EVENT_PAYMENT
    )
    event = json.loads(payment_call.args[1])["dict"]
    assert event["paymentSourceType"] == "Web3CardanoV1"
