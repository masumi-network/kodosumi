"""
Tests for optional Web3CardanoV2 registration and payment support.

The selling wallet decides the rail: a wallet of a Web3CardanoV2 payment
source registers a V2 agent, prices it inside supportedPaymentSources, and
pays with a supportedPaymentSourceIndex. Everything else stays on V1.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kodosumi.config import MasumiConfig
from kodosumi.runner.main import _parse_source_index
from kodosumi.runner.payment import MasumiClient
from kodosumi.service.expose.registry import (
    PAYMENT_SOURCE_TYPE_V1,
    PAYMENT_SOURCE_TYPE_V2,
    get_registration_status,
    list_wallets,
    register_agent,
    registry_pricing_to_supported_sources,
)
from kodosumi.service.sumi.models import JobStatusResponse


def _make_config(**overrides) -> MasumiConfig:
    defaults = dict(
        network="Preprod",
        base_url="https://test.masumi.network/api/v1",
        token="test-token",
        poll_interval=1.0,
    )
    defaults.update(overrides)
    return MasumiConfig(**defaults)


def _json_response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.text = ""
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


def _patch_registry_client(get_responses=None, post_response=None):
    """Patch HTTPXClient in the registry module and return the mock client."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    if get_responses is not None:
        client.get.side_effect = list(get_responses)
    if post_response is not None:
        client.post.return_value = post_response
    factory = MagicMock(return_value=client)
    return client, patch(
        "kodosumi.service.expose.registry.HTTPXClient", factory)


class TestRegistryPricingToSupportedSources:
    """Tests for the V1 pricing to V2 supportedPaymentSources conversion."""

    def test_fixed_pricing(self):
        sources = registry_pricing_to_supported_sources(
            {"pricingType": "Fixed",
             "Pricing": [{"amount": "10000000", "unit": ""}]},
            "Preprod",
            "addr_test1contract",
        )
        assert sources == [{
            "chain": "Cardano",
            "network": "Preprod",
            "paymentSourceType": "Web3CardanoV2",
            "address": "addr_test1contract",
            "pricing": {
                "pricingType": "Fixed",
                "fixed": [{"asset": "", "amount": "10000000"}],
            },
        }]

    def test_fixed_pricing_keeps_token_unit(self):
        sources = registry_pricing_to_supported_sources(
            {"pricingType": "Fixed",
             "Pricing": [{"amount": "10000", "unit": "16a55b2a0014df10"}]},
            "Mainnet",
            "addr1contract",
        )
        fixed = sources[0]["pricing"]["fixed"]
        assert fixed == [{"asset": "16a55b2a0014df10", "amount": "10000"}]
        assert sources[0]["network"] == "Mainnet"

    def test_amount_is_stringified(self):
        sources = registry_pricing_to_supported_sources(
            {"pricingType": "Fixed", "Pricing": [{"amount": 5000, "unit": ""}]},
            "Preprod",
            "addr_test1contract",
        )
        assert sources[0]["pricing"]["fixed"][0]["amount"] == "5000"

    def test_free_pricing_has_no_amounts(self):
        sources = registry_pricing_to_supported_sources(
            {"pricingType": "Free"}, "Preprod", "addr_test1contract")
        assert sources[0]["pricing"] == {"pricingType": "Free"}

    def test_missing_contract_address_raises(self):
        with pytest.raises(ValueError):
            registry_pricing_to_supported_sources(
                {"pricingType": "Free"}, "Preprod", "")


class TestRegisterAgentPayload:
    """The registration body must carry exactly one pricing shape."""

    @pytest.mark.asyncio
    async def test_v1_sends_agent_pricing_only(self):
        client, patcher = _patch_registry_client(
            post_response=_json_response({"data": {"id": "reg1"}}))
        with patcher:
            await register_agent(
                masumi=_make_config(),
                name="agent",
                description="",
                api_base_url="https://host/sumi/flow",
                tags=["tag"],
                pricing={"pricingType": "Free"},
                wallet_vkey="vkey",
            )
        body = client.post.call_args.kwargs["json"]
        assert body["AgentPricing"] == {"pricingType": "Free"}
        assert "supportedPaymentSources" not in body

    @pytest.mark.asyncio
    async def test_v2_sends_supported_sources_only(self):
        sources = registry_pricing_to_supported_sources(
            {"pricingType": "Free"}, "Preprod", "addr_test1contract")
        client, patcher = _patch_registry_client(
            post_response=_json_response({"data": {"id": "reg2"}}))
        with patcher:
            await register_agent(
                masumi=_make_config(),
                name="agent",
                description="",
                api_base_url="https://host/sumi/flow",
                tags=["tag"],
                pricing=None,
                wallet_vkey="vkey",
                supported_payment_sources=sources,
            )
        body = client.post.call_args.kwargs["json"]
        assert body["supportedPaymentSources"] == sources
        assert "AgentPricing" not in body


class TestListWallets:
    """Wallet listing must expose the payment source of every wallet."""

    @pytest.mark.asyncio
    async def test_embedded_wallets_carry_source_type(self):
        payload = {"data": {"PaymentSources": [{
            "id": "src1",
            "network": "Preprod",
            "paymentSourceType": PAYMENT_SOURCE_TYPE_V2,
            "smartContractAddress": "addr_test1contract",
            "SellingWallets": [
                {"walletVkey": "vkey1", "walletAddress": "addr_test1w",
                 "note": "seller"},
            ],
        }]}}
        client, patcher = _patch_registry_client(
            get_responses=[_json_response(payload)])
        with patcher:
            wallets = await list_wallets(_make_config())
        assert wallets == [{
            "walletVkey": "vkey1",
            "walletAddress": "addr_test1w",
            "sourceId": "src1",
            "note": "seller",
            "network": "Preprod",
            "paymentSourceType": PAYMENT_SOURCE_TYPE_V2,
            "smartContractAddress": "addr_test1contract",
        }]
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_wallet_endpoint(self):
        sources = {"data": {"PaymentSources": [{
            "id": "src1",
            "network": "Preprod",
            "paymentSourceType": PAYMENT_SOURCE_TYPE_V2,
            "smartContractAddress": "addr_test1contract",
        }]}}
        wallets_page = {"data": {"Wallets": [
            {"walletVkey": "vkey1", "walletAddress": "addr_test1w",
             "note": None},
        ]}}
        client, patcher = _patch_registry_client(
            get_responses=[_json_response(sources),
                           _json_response(wallets_page)])
        with patcher:
            wallets = await list_wallets(_make_config())
        assert len(wallets) == 1
        assert wallets[0]["walletVkey"] == "vkey1"
        assert wallets[0]["note"] == ""
        assert wallets[0]["paymentSourceType"] == PAYMENT_SOURCE_TYPE_V2
        fallback_url = client.get.call_args_list[1].args[0]
        assert "/wallet?paymentSourceId=src1" in fallback_url
        assert "walletType=Selling" in fallback_url

    @pytest.mark.asyncio
    async def test_skips_sources_of_other_networks(self):
        payload = {"data": {"PaymentSources": [{
            "id": "src-main",
            "network": "Mainnet",
            "paymentSourceType": PAYMENT_SOURCE_TYPE_V1,
            "smartContractAddress": "addr1contract",
            "SellingWallets": [{"walletVkey": "vkey-main"}],
        }]}}
        client, patcher = _patch_registry_client(
            get_responses=[_json_response(payload)])
        with patcher:
            wallets = await list_wallets(_make_config(network="Preprod"))
        assert wallets == []


class TestGetRegistrationStatusFilter:
    """The registry list defaults to V1, so V2 lookups must say so."""

    @pytest.mark.asyncio
    async def test_v2_lookup_filters_by_payment_source_type(self):
        client, patcher = _patch_registry_client(
            get_responses=[_json_response({"data": {"Assets": [
                {"id": "reg2", "agentIdentifier": "agent2"},
            ]}})])
        with patcher:
            result = await get_registration_status(
                _make_config(),
                registration_id="reg2",
                payment_source_type=PAYMENT_SOURCE_TYPE_V2,
            )
        assert result is not None
        assert result["id"] == "reg2"
        url = client.get.call_args.args[0]
        assert "filterPaymentSourceType=Web3CardanoV2" in url

    @pytest.mark.asyncio
    async def test_v1_lookup_sends_no_filter(self):
        client, patcher = _patch_registry_client(
            get_responses=[_json_response({"data": {"Assets": [
                {"id": "reg1"},
            ]}})])
        with patcher:
            await get_registration_status(
                _make_config(), registration_id="reg1")
        assert "filterPaymentSourceType" not in client.get.call_args.args[0]


class TestInitPaymentSourceIndex:
    """The payment node requires the index on V2 and rejects it on V1."""

    def _patch_payment_client(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post.return_value = _json_response(
            {"data": {"blockchainIdentifier": "abc"}})
        return client, patch("httpx.AsyncClient", MagicMock(
            return_value=client))

    @pytest.mark.asyncio
    async def test_index_sent_when_set(self):
        client, patcher = self._patch_payment_client()
        with patcher:
            await MasumiClient(_make_config()).init_payment(
                agent_identifier="agent",
                network="Preprod",
                input_hash="hash",
                identifier_from_purchaser="purchaser",
                supported_payment_source_index=0,
            )
        payload = client.post.call_args.kwargs["json"]
        assert payload["supportedPaymentSourceIndex"] == 0

    @pytest.mark.asyncio
    async def test_index_omitted_when_none(self):
        client, patcher = self._patch_payment_client()
        with patcher:
            await MasumiClient(_make_config()).init_payment(
                agent_identifier="agent",
                network="Preprod",
                input_hash="hash",
                identifier_from_purchaser="purchaser",
            )
        payload = client.post.call_args.kwargs["json"]
        assert "supportedPaymentSourceIndex" not in payload


class TestJobStatusResponseEcho:
    """The buyer reads the rail off the MIP-003 responses."""

    def test_v2_fields_round_trip(self):
        response = JobStatusResponse(
            job_id="job1",
            status="awaiting_payment",
            blockchainIdentifier="abc",
            paymentSourceType=PAYMENT_SOURCE_TYPE_V2,
            supportedPaymentSourceIndex=0,
        )
        dumped = response.model_dump()
        assert dumped["paymentSourceType"] == "Web3CardanoV2"
        # Index 0 is a real selection and must survive serialisation.
        assert dumped["supportedPaymentSourceIndex"] == 0

    def test_v1_leaves_both_fields_empty(self):
        response = JobStatusResponse(job_id="job1", status="running")
        dumped = response.model_dump()
        assert dumped["paymentSourceType"] is None
        assert dumped["supportedPaymentSourceIndex"] is None


class TestParseSourceIndex:
    """Absent intent must never coerce into index 0."""

    def test_int_index(self):
        assert _parse_source_index(0) == 0
        assert _parse_source_index(3) == 3

    def test_numeric_string(self):
        assert _parse_source_index("2") == 2
        assert _parse_source_index(" 2 ") == 2

    def test_absent_and_junk_values(self):
        for value in (None, "", "abc", [], {}, False, True, -1, 1.5):
            assert _parse_source_index(value) is None
