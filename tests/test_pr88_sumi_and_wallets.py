"""Regression tests for PR 88 serving and wallet-list findings."""

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from litestar.exceptions import ClientException

from kodosumi.config import MasumiConfig
from kodosumi.const import DB_FILE
from kodosumi.service.expose.migrate_control import RegistryMigrateControl
from kodosumi.service.expose.models import ExposeMeta
from kodosumi.service.expose.registry import (MAX_CONCURRENT_WALLET_REQUESTS,
                                              list_wallets)
from kodosumi.service.sumi.control import SumiControl
from kodosumi.service.sumi.jobs import (_advance_pending_migration,
                                        _get_job_status_from_db,
                                        _heal_agent_identifier)
from kodosumi.service.sumi.models import AwaitingInputSchema, JobStatusResponse

MIGRATE = RegistryMigrateControl.migrate.fn


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


@pytest.mark.asyncio
async def test_healer_does_not_return_an_id_for_a_replaced_registration():
    meta = ExposeMeta(url="/flow", data="registrationId: old-reg\n")
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "state": "RegistrationConfirmed",
                "agentIdentifier": "old-agent",
            },
        ),
        patch(
            "kodosumi.service.expose.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.flow_meta.update_flow_meta",
            new_callable=AsyncMock,
            return_value=None,
        ) as write,
    ):
        result = await _heal_agent_identifier(
            "expose",
            meta,
            {"registrationId": "old-reg", "network": "Preprod"},
            _state(),
        )

    assert result is None
    assert write.call_args.kwargs["expected"] == {
        "registrationId": "old-reg",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registration_state",
    ["RegistrationInitiated", "RegistrationFailed"],
)
async def test_healer_rejects_unconfirmed_registration_states(
    registration_state,
):
    meta = ExposeMeta(url="/flow", data="registrationId: pending-reg\n")
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.registry.get_registration_status",
            new_callable=AsyncMock,
            return_value={
                "state": registration_state,
                "agentIdentifier": "pending-agent",
            },
        ),
        patch(
            "kodosumi.service.expose.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.flow_meta.update_flow_meta",
            new_callable=AsyncMock,
        ) as write,
    ):
        result = await _heal_agent_identifier(
            "expose",
            meta,
            {"registrationId": "pending-reg", "network": "Preprod"},
            _state(),
        )

    assert result is None
    write.assert_not_called()


@pytest.mark.asyncio
async def test_job_rereads_metadata_when_another_request_finishes_migration():
    stale = {
        "agentIdentifier": "v1-agent",
        "registrationId": "v1-reg",
        "pendingMigration": {"registrationId": "v2-reg"},
    }
    fresh = {
        "agentIdentifier": "v2-agent",
        "registrationId": "v2-reg",
        "paymentSourceType": "Web3CardanoV2",
    }
    meta = ExposeMeta(url="/flow", data=yaml.dump(stale))
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    with (
        patch(
            "kodosumi.service.expose.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.migration.advance_migration",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "kodosumi.service.expose.flow_meta.get_flow_meta",
            return_value=fresh,
        ),
    ):
        result = await _advance_pending_migration(
            "expose", meta, stale, "Preprod", _state())

    assert result == fresh


@pytest.mark.asyncio
async def test_awaiting_input_keeps_all_payment_fields(tmp_path):
    job_id = "job-1"
    db_file = Path(tmp_path) / "user" / job_id / DB_FILE
    db_file.parent.mkdir(parents=True)
    db_file.touch()
    status = JobStatusResponse(
        job_id=job_id,
        status="awaiting_input",
        blockchainIdentifier="chain-id",
        payByTime=123,
        input_hash="input-hash",
        agentIdentifier="agent-id",
        sellerVKey="seller-vkey",
        paymentSourceType="Web3CardanoV2",
        supportedPaymentSourceIndex=0,
    )
    awaiting = AwaitingInputSchema(input_groups=[])
    state = {"settings": SimpleNamespace(EXEC_DIR=str(tmp_path))}
    with (
        patch(
            "kodosumi.service.sumi.control._get_job_status_from_db",
            new_callable=AsyncMock,
            return_value=(status, ["lock-1"]),
        ),
        patch(
            "kodosumi.service.sumi.control._fetch_lock_input_schemas",
            new_callable=AsyncMock,
            return_value=awaiting,
        ),
    ):
        result = await SumiControl._get_job_status_impl(
            None, state, job_id)

    assert result.input_schema == awaiting
    assert result.blockchainIdentifier == "chain-id"
    assert result.payByTime == 123
    assert result.input_hash == "input-hash"
    assert result.sellerVKey == "seller-vkey"
    assert result.paymentSourceType == "Web3CardanoV2"
    assert result.supportedPaymentSourceIndex == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "message", "expected_hash"),
    [
        (
            "meta",
            '{"type":"dict","dict":{"extra":'
            '{"input_hash":"metadata-hash"}}}',
            "metadata-hash",
        ),
        (
            "payment",
            '{"type":"dict","dict":{"step":"initialized",'
            '"inputHash":"payment-hash"}}',
            "payment-hash",
        ),
    ],
)
async def test_real_status_restores_input_hash(
    kind,
    message,
    expected_hash,
):
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("""
            CREATE TABLE monitor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                kind TEXT,
                message TEXT
            )
        """)
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (1.0, "status", "payment"),
        )
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (2.0, kind, message),
        )
        conn.commit()

        result, _ = await _get_job_status_from_db(conn, "job-1")
    finally:
        conn.close()

    assert result.input_hash == expected_hash


def _response(data: dict):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = data
    return response


@pytest.mark.asyncio
async def test_payment_sources_are_paginated_before_network_filtering():
    first_page = [{
        "id": f"mainnet-{index}",
        "network": "Mainnet",
        "SellingWallets": [{"walletVkey": f"mainnet-vkey-{index}"}],
    } for index in range(100)]
    target = {
        "id": "preprod-source",
        "network": "Preprod",
        "paymentSourceType": "Web3CardanoV2",
        "smartContractAddress": "addr_test1contract",
        "SellingWallets": [{
            "walletVkey": "target-vkey",
            "walletAddress": "addr_test1wallet",
        }],
    }
    second_page = [first_page[-1], target]
    client = AsyncMock()
    client.get.side_effect = [
        _response({"data": {"PaymentSources": first_page}}),
        _response({"data": {"PaymentSources": second_page}}),
    ]
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "kodosumi.service.expose.registry.HTTPXClient",
        return_value=context,
    ):
        wallets = await list_wallets(_config())

    assert [wallet["walletVkey"] for wallet in wallets] == ["target-vkey"]
    assert client.get.call_count == 2
    second_url = client.get.call_args_list[1].args[0]
    assert "take=100" in second_url
    assert "cursorId=mainnet-99" in second_url


@pytest.mark.asyncio
async def test_later_payment_source_page_failure_keeps_prior_pages():
    first_page = [{
        "id": f"source-{index}",
        "network": "Preprod",
        "paymentSourceType": "Web3CardanoV2",
        "SellingWallets": [{
            "walletVkey": f"vkey-{index}",
            "walletAddress": f"address-{index}",
        }],
    } for index in range(100)]
    client = AsyncMock()
    client.get.side_effect = [
        _response({"data": {"PaymentSources": first_page}}),
        OSError("second page unavailable"),
    ]
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "kodosumi.service.expose.registry.HTTPXClient",
        return_value=context,
    ):
        wallets = await list_wallets(_config())

    assert len(wallets) == 100
    assert wallets[0]["walletVkey"] == "vkey-0"
    assert wallets[-1]["walletVkey"] == "vkey-99"
    assert client.get.call_count == 2


@pytest.mark.asyncio
async def test_registration_inventory_rejects_a_partial_source_list():
    first_page = [{
        "id": f"source-{index}",
        "network": "Preprod",
        "paymentSourceType": "Web3CardanoV2",
        "SellingWallets": [{
            "walletVkey": f"vkey-{index}",
            "walletAddress": f"address-{index}",
        }],
    } for index in range(100)]
    client = AsyncMock()
    client.get.side_effect = [
        _response({"data": {"PaymentSources": first_page}}),
        OSError("second page unavailable"),
    ]
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "kodosumi.service.expose.registry.HTTPXClient",
        return_value=context,
    ):
        with pytest.raises(RuntimeError, match="complete payment source"):
            await list_wallets(_config(), require_complete=True)


@pytest.mark.asyncio
async def test_selling_wallets_are_paginated_per_source():
    sources = [{
        "id": "source-a",
        "network": "Preprod",
        "paymentSourceType": "Web3CardanoV1",
        "smartContractAddress": "addr_test1contracta",
    }, {
        "id": "source-b",
        "network": "Preprod",
        "paymentSourceType": "Web3CardanoV2",
        "smartContractAddress": "addr_test1contractb",
    }]
    first_wallet_page = [{
        "id": f"wallet-{index}",
        "paymentSourceId": "source-a",
        "walletVkey": f"vkey-{index}",
        "walletAddress": f"addr-{index}",
    } for index in range(100)]
    last_wallet = {
        "id": "wallet-100",
        "paymentSourceId": "source-b",
        "walletVkey": "vkey-100",
        "walletAddress": "addr-100",
    }
    wallet_urls = []

    async def get_response(url, headers):
        if "/payment-source?" in url:
            return _response({"data": {"PaymentSources": sources}})

        wallet_urls.append(url)
        if "paymentSourceId=source-a" in url:
            page = [first_wallet_page[-1]] \
                if "cursorId=" in url else first_wallet_page
        elif "paymentSourceId=source-b" in url:
            page = [last_wallet]
        return _response({"data": {"Wallets": page}})

    client = AsyncMock()
    client.get.side_effect = get_response
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "kodosumi.service.expose.registry.HTTPXClient",
        return_value=context,
    ):
        wallets = await list_wallets(_config())

    assert len(wallets) == 101
    assert wallets[0]["sourceId"] == "source-a"
    assert wallets[-1]["sourceId"] == "source-b"
    assert all("paymentSourceId=" in url for url in wallet_urls)
    source_a_urls = [
        url for url in wallet_urls if "paymentSourceId=source-a" in url
    ]
    source_b_urls = [
        url for url in wallet_urls if "paymentSourceId=source-b" in url
    ]
    assert len(source_a_urls) == 2
    assert len(source_b_urls) == 1
    assert "walletType=Selling" in source_a_urls[0]
    assert "take=100" in source_a_urls[0]
    assert "cursorId=wallet-99" in source_a_urls[1]


@pytest.mark.asyncio
async def test_wallet_fetches_use_the_named_concurrency_limit():
    sources = [{
        "id": f"source-{index}",
        "network": "Preprod",
        "paymentSourceType": "Web3CardanoV2",
    } for index in range(MAX_CONCURRENT_WALLET_REQUESTS + 5)]
    client = AsyncMock()
    client.get.return_value = _response({
        "data": {"PaymentSources": sources},
    })
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    active_requests = 0
    max_active_requests = 0

    async def fetch_wallets(
        _client, _masumi, _headers, source_id, _require_complete=False,
        _report=None
    ):
        nonlocal active_requests, max_active_requests
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        await asyncio.sleep(0)
        active_requests -= 1
        return [{
            "walletVkey": f"vkey-{source_id}",
            "walletAddress": f"addr-{source_id}",
        }]

    with (
        patch(
            "kodosumi.service.expose.registry.HTTPXClient",
            return_value=context,
        ),
        patch(
            "kodosumi.service.expose.registry._list_source_selling_wallets",
            new=fetch_wallets,
        ),
    ):
        wallets = await list_wallets(_config())

    assert len(wallets) == len(sources)
    assert max_active_requests == MAX_CONCURRENT_WALLET_REQUESTS


@pytest.mark.asyncio
async def test_failed_source_fetch_keeps_embedded_wallets():
    sources = [{
        "id": "embedded-source",
        "network": "Preprod",
        "paymentSourceType": "Web3CardanoV2",
        "SellingWallets": [{
            "walletVkey": "embedded-vkey",
            "walletAddress": "embedded-address",
        }],
    }, {
        "id": "failed-source",
        "network": "Preprod",
        "paymentSourceType": "Web3CardanoV2",
    }]
    client = AsyncMock()
    client.get.return_value = _response({
        "data": {"PaymentSources": sources},
    })
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "kodosumi.service.expose.registry.HTTPXClient",
            return_value=context,
        ),
        patch(
            "kodosumi.service.expose.registry._list_source_selling_wallets",
            new_callable=AsyncMock,
            side_effect=OSError("source unavailable"),
        ),
    ):
        wallets = await list_wallets(_config())

    assert [wallet["walletVkey"] for wallet in wallets] == [
        "embedded-vkey",
    ]


@pytest.mark.asyncio
async def test_migrate_rejects_string_boolean_before_external_calls():
    row = {"name": "expose", "network": "Preprod", "meta": "[]"}
    meta = {
        "display": "Agent",
        "agentIdentifier": "v1-agent",
        "registrationId": "v1-reg",
        "agentPricing": [{"pricingType": "Free"}],
    }
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
        ) as wallets,
        patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock,
        ) as register,
    ):
        with pytest.raises(ClientException) as error:
            await MIGRATE(
                None,
                name="expose",
                data={
                    "flow_url": "/flow",
                    "wallet_vkey": "vkey-v2",
                    "deregister_previous": "false",
                },
                state=_state(),
            )

    assert error.value.status_code == 422
    wallets.assert_not_called()
    register.assert_not_called()
