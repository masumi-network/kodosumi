"""
Tests for the evidence a wallet listing keeps about itself.

An empty selling wallet list used to read "No selling wallets found for
network X. Check your Masumi Payment API token and configuration" no
matter what caused it, and the migrate dialog turned the same emptiness
into "No Web3CardanoV2 selling wallet on this network". Both sentences
name a cause the code never checked. These tests pin the causes apart.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kodosumi.config import MasumiConfig
from kodosumi.service.expose.registry import list_wallets
from kodosumi.service.expose.wallet_inventory import WalletReport


def _make_config(**overrides) -> MasumiConfig:
    defaults = dict(
        network="Mainnet",
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
    return response


def _patch_registry_client(get_responses):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.get.side_effect = list(get_responses)
    factory = MagicMock(return_value=client)
    return client, patch(
        "kodosumi.service.expose.registry.HTTPXClient", factory)


class TestDescribeEmpty:
    """Every cause of an empty list gets its own sentence."""

    def test_failed_request_is_quoted(self):
        report = WalletReport(
            source_count=1,
            networks=["Mainnet"],
            problems=["GET /payment-source answered HTTP 401"],
        )
        message = report.describe_empty("Mainnet")
        assert "HTTP 401" in message
        assert "no selling wallet" not in message.lower()

    def test_no_visible_source_blames_the_token_or_the_node(self):
        message = WalletReport().describe_empty("Mainnet")
        assert "no payment source at all" in message
        assert "KODO_MASUMI" in message

    def test_other_network_names_what_the_token_sees(self):
        report = WalletReport(
            source_count=2, matched_count=0, networks=["Preprod"])
        message = report.describe_empty("Mainnet")
        assert "Preprod" in message
        assert "network limit" in message

    def test_right_network_without_a_wallet_asks_for_a_wallet(self):
        report = WalletReport(
            source_count=1, matched_count=1, networks=["Mainnet"])
        message = report.describe_empty("Mainnet")
        assert "has a selling wallet yet" in message

    def test_a_source_without_a_network_still_counts_as_matched(self):
        # list_wallets keeps a source whose row carries no network, so a
        # report that judged by the networks list alone would blame the
        # token for a source it did read.
        report = WalletReport(source_count=1, matched_count=1, networks=[])
        message = report.describe_empty("Mainnet")
        assert "has a selling wallet yet" in message
        assert "network limit" not in message

    def test_a_clean_run_has_nothing_to_warn_about(self):
        assert WalletReport(source_count=1, matched_count=1) \
            .describe_partial() is None

    def test_a_failed_request_warns_even_with_wallets_in_hand(self):
        report = WalletReport(
            source_count=2, matched_count=2,
            problems=["GET /wallet for payment source src-v2 answered "
                      "HTTP 503"])
        warning = report.describe_partial()
        assert "may be incomplete" in warning
        assert "HTTP 503" in warning


class TestListWalletsReport:
    """The report carries what the node showed, not what was asked for."""

    @pytest.mark.asyncio
    async def test_records_every_visible_source_before_the_filter(self):
        # A token limited to Preprod answers 200 with Preprod rows only.
        # The wallet list is empty for Mainnet, and only the report can
        # say that the token never saw a Mainnet source.
        sources = _json_response({"data": {"PaymentSources": [
            {"id": "src-preprod", "network": "Preprod",
             "paymentSourceType": "Web3CardanoV1"},
        ]}})
        report = WalletReport()
        _, patched = _patch_registry_client([sources])
        with patched:
            wallets = await list_wallets(_make_config(), report=report)

        assert wallets == []
        assert report.source_count == 1
        assert report.networks == ["Preprod"]
        assert report.problems == []
        assert "Preprod" in report.describe_empty("Mainnet")

    @pytest.mark.asyncio
    async def test_records_a_refused_payment_source_request(self):
        report = WalletReport()
        _, patched = _patch_registry_client(
            [_json_response({}, status_code=401)])
        with patched:
            wallets = await list_wallets(_make_config(), report=report)

        assert wallets == []
        assert report.problems == [
            "GET /payment-source answered HTTP 401"]
        assert "HTTP 401" in report.describe_empty("Mainnet")

    @pytest.mark.asyncio
    async def test_records_a_refused_wallet_request(self):
        sources = _json_response({"data": {"PaymentSources": [
            {"id": "src-main", "network": "Mainnet",
             "paymentSourceType": "Web3CardanoV2"},
        ]}})
        report = WalletReport()
        _, patched = _patch_registry_client(
            [sources, _json_response({}, status_code=403)])
        with patched:
            wallets = await list_wallets(_make_config(), report=report)

        assert wallets == []
        assert report.source_count == 1
        assert report.problems == [
            "GET /wallet for payment source src-main answered HTTP 403"]

    @pytest.mark.asyncio
    async def test_a_clean_run_reports_no_problem(self):
        sources = _json_response({"data": {"PaymentSources": [
            {"id": "src-main", "network": "Mainnet",
             "paymentSourceType": "Web3CardanoV2",
             "smartContractAddress": "addr_test1contract"},
        ]}})
        wallets_page = _json_response({"data": {"Wallets": [
            {"id": "w1", "walletVkey": "vkey-1",
             "walletAddress": "addr1", "note": "seller"},
        ]}})
        report = WalletReport()
        _, patched = _patch_registry_client([sources, wallets_page])
        with patched:
            wallets = await list_wallets(_make_config(), report=report)

        assert [w["walletVkey"] for w in wallets] == ["vkey-1"]
        assert wallets[0]["paymentSourceType"] == "Web3CardanoV2"
        assert report.problems == []

    @pytest.mark.asyncio
    async def test_records_a_payment_source_page_it_could_not_follow(self):
        # The node's cursor is the id of the last row. A full page whose
        # last row carries no id ends the paging early, and the sources
        # beyond it are never read. Without a recorded problem the caller
        # reads that truncated list as the whole list, and the panel says
        # "add a wallet" about a V2 source it never saw.
        # Every row on this page is for another network, so no wallet
        # request follows and the truncation is the only event.
        page = [{"id": f"src-{i}", "network": "Preprod",
                 "paymentSourceType": "Web3CardanoV1"} for i in range(99)]
        page.append({"network": "Preprod",
                     "paymentSourceType": "Web3CardanoV1"})
        report = WalletReport()
        _, patched = _patch_registry_client(
            [_json_response({"data": {"PaymentSources": page}})])
        with patched:
            await list_wallets(_make_config(), report=report)

        assert len(report.problems) == 1
        assert "could not be paged past" in report.problems[0]
        assert "may be incomplete" in report.describe_partial()

    @pytest.mark.asyncio
    async def test_records_a_wallet_page_it_could_not_follow(self):
        sources = _json_response({"data": {"PaymentSources": [
            {"id": "src-main", "network": "Mainnet",
             "paymentSourceType": "Web3CardanoV2"},
        ]}})
        wallet_page = [{"id": f"w-{i}", "walletVkey": f"vkey-{i}",
                        "walletAddress": "addr1", "note": "seller"}
                       for i in range(99)]
        wallet_page.append({"walletVkey": "vkey-last",
                            "walletAddress": "addr1", "note": "seller"})
        report = WalletReport()
        _, patched = _patch_registry_client(
            [sources, _json_response({"data": {"Wallets": wallet_page}})])
        with patched:
            wallets = await list_wallets(_make_config(), report=report)

        # The list that did arrive is still offered. It is the silence
        # about the rest that this guards against.
        assert len(wallets) == 100
        assert len(report.problems) == 1
        assert "could not be paged past" in report.problems[0]
        assert "src-main" in report.problems[0]

    @pytest.mark.asyncio
    async def test_records_a_wallet_request_that_raised(self):
        # asyncio.gather returns the exception rather than raising it, and
        # an unrecorded exception left the panel saying "add a wallet"
        # about a node it could not reach.
        sources = _json_response({"data": {"PaymentSources": [
            {"id": "src-main", "network": "Mainnet",
             "paymentSourceType": "Web3CardanoV2"},
        ]}})
        report = WalletReport()
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.get.side_effect = [sources, OSError("connection refused")]
        with patch("kodosumi.service.expose.registry.HTTPXClient",
                   MagicMock(return_value=client)):
            wallets = await list_wallets(_make_config(), report=report)

        assert wallets == []
        assert len(report.problems) == 1
        assert "connection refused" in report.problems[0]
        assert "connection refused" in report.describe_empty("Mainnet")


class TestWalletsEndpoint:
    """The endpoint answers with the node's network name and its evidence."""

    def _state(self, config_name):
        settings = MagicMock()
        settings.get_masumi.return_value = _make_config(network=config_name)
        return {"settings": settings}

    async def _call(self, list_impl, config_name="Mainnet"):
        from kodosumi.service.expose.wallet_control import WalletsControl
        handler = getattr(WalletsControl.list_wallets, "fn",
                          WalletsControl.list_wallets)
        with patch("kodosumi.service.expose.wallet_control.db.init_database",
                   new_callable=AsyncMock), \
             patch("kodosumi.service.expose.wallet_control.db.get_expose",
                   new_callable=AsyncMock,
                   return_value={"name": "x", "network": config_name}), \
             patch("kodosumi.service.expose.registry.list_wallets",
                   new=list_impl):
            return await handler(WalletsControl, name="x",
                                 state=self._state(config_name))

    @pytest.mark.asyncio
    async def test_empty_list_quotes_the_network_the_node_knows(self):
        # The expose row holds the KODO_MASUMI entry name, which is free
        # text. Comparing it against the node's own network values made the
        # endpoint blame the token for a payment source it had just read.
        async def fake(masumi, require_complete=False, report=None):
            report.source_count = 1
            report.matched_count = 1
            report.networks = ["Mainnet"]
            return []

        result = await self._call(fake, config_name="Mainnet-prod")
        assert result["wallets"] == []
        assert "has a selling wallet yet" in result["error"]
        assert "Mainnet-prod" not in result["error"]

    @pytest.mark.asyncio
    async def test_a_partial_list_is_returned_with_a_warning(self):
        async def fake(masumi, require_complete=False, report=None):
            report.source_count = 2
            report.matched_count = 2
            report.problems.append(
                "GET /wallet for payment source src-v2 answered HTTP 503")
            return [{"walletVkey": "vkey-1",
                     "paymentSourceType": "Web3CardanoV1"}]

        result = await self._call(fake)
        assert len(result["wallets"]) == 1
        assert "error" not in result
        assert "HTTP 503" in result["warning"]

    @pytest.mark.asyncio
    async def test_a_clean_list_carries_no_warning(self):
        async def fake(masumi, require_complete=False, report=None):
            report.source_count = 1
            report.matched_count = 1
            return [{"walletVkey": "vkey-1",
                     "paymentSourceType": "Web3CardanoV2"}]

        result = await self._call(fake)
        assert "warning" not in result
        assert "error" not in result
