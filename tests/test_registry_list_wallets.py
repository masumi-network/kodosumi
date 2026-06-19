"""
Unit tests for registry.list_wallets() — schema-tolerant wallet listing.

Covers:
1. OLD schema (SellingWallets inline in /payment-source) → correct wallets returned.
2. NEW schema (no SellingWallets, /wallet/list paginated) → all wallets returned.
3. Inclusive-cursor pagination: last item of page N is first item of page N+1
   (i.e. duplicate) → deduplication is applied, no infinite loop, all unique
   wallets are returned.
4. take >= 2 invariant: take=100 is always used (never take=1).
5. Non-200 from /payment-source → returns [] (existing error-handling behavior).
6. Non-200 from /wallet/list → partial result (empty for that source), no crash.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from kodosumi.config import MasumiConfig
from kodosumi.service.expose.registry import list_wallets, _list_selling_wallets_v2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_masumi(base_url: str = "https://api.example.com", network: str = "Preprod") -> MasumiConfig:
    return MasumiConfig(network=network, base_url=base_url, token="test-token")


def mock_response(status_code: int, json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


def make_httpx_client_patch(responses: list):
    """
    Return a context manager mock for HTTPXClient that replays `responses`
    in order for each .get() call.

    Each element of `responses` is a MagicMock with .status_code and .json().
    """
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(side_effect=responses)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client_mock


# ---------------------------------------------------------------------------
# Test 1: OLD schema — SellingWallets inline in /payment-source
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_old_schema_returns_inline_wallets():
    """OLD API: SellingWallets[] present in each PaymentSource — must be returned."""
    masumi = make_masumi()

    payment_source_resp = mock_response(200, {
        "data": {
            "PaymentSources": [
                {
                    "id": "src-1",
                    "SellingWallets": [
                        {"walletVkey": "vkey1", "walletAddress": "addr1", "note": "wallet A"},
                        {"walletVkey": "vkey2", "walletAddress": "addr2", "note": "wallet B"},
                    ],
                }
            ]
        }
    })

    cm, _ = make_httpx_client_patch([payment_source_resp])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        result = await list_wallets(masumi)

    assert len(result) == 2
    assert result[0] == {"walletVkey": "vkey1", "walletAddress": "addr1", "sourceId": "src-1", "note": "wallet A"}
    assert result[1] == {"walletVkey": "vkey2", "walletAddress": "addr2", "sourceId": "src-1", "note": "wallet B"}


@pytest.mark.asyncio
async def test_old_schema_multiple_sources():
    """OLD API: Multiple PaymentSources each with SellingWallets."""
    masumi = make_masumi()

    payment_source_resp = mock_response(200, {
        "data": {
            "PaymentSources": [
                {
                    "id": "src-1",
                    "SellingWallets": [
                        {"walletVkey": "vkey1", "walletAddress": "addr1", "note": ""},
                    ],
                },
                {
                    "id": "src-2",
                    "SellingWallets": [
                        {"walletVkey": "vkey2", "walletAddress": "addr2", "note": ""},
                        {"walletVkey": "vkey3", "walletAddress": "addr3", "note": ""},
                    ],
                },
            ]
        }
    })

    cm, _ = make_httpx_client_patch([payment_source_resp])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        result = await list_wallets(masumi)

    assert len(result) == 3
    source_ids = {w["sourceId"] for w in result}
    assert source_ids == {"src-1", "src-2"}


# ---------------------------------------------------------------------------
# Test 2: NEW schema — no SellingWallets, /wallet/list single page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_schema_single_page():
    """NEW API: No SellingWallets key; /wallet/list returns all wallets in one page."""
    masumi = make_masumi()

    payment_source_resp = mock_response(200, {
        "data": {
            "PaymentSources": [
                {"id": "src-new-1"},  # no SellingWallets key
            ]
        }
    })

    wallet_list_resp = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "w1", "walletVkey": "vkeyA", "walletAddress": "addrA", "note": "new-wallet-1"},
                {"id": "w2", "walletVkey": "vkeyB", "walletAddress": "addrB", "note": "new-wallet-2"},
            ]
        }
    })

    # Second call to /wallet/list with cursor → no new ids → terminates
    wallet_list_cursor_resp = mock_response(200, {
        "data": {
            "Wallets": [
                # Only the last item from previous page (inclusive cursor duplicate)
                {"id": "w2", "walletVkey": "vkeyB", "walletAddress": "addrB", "note": "new-wallet-2"},
            ]
        }
    })

    cm, client_mock = make_httpx_client_patch([
        payment_source_resp,
        wallet_list_resp,
        wallet_list_cursor_resp,
    ])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        result = await list_wallets(masumi)

    assert len(result) == 2
    vkeys = {w["walletVkey"] for w in result}
    assert vkeys == {"vkeyA", "vkeyB"}
    for w in result:
        assert w["sourceId"] == "src-new-1"


# ---------------------------------------------------------------------------
# Test 3: NEW schema — paginated with inclusive cursor (duplicates between pages)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_schema_inclusive_cursor_pagination():
    """
    NEW API with multi-page /wallet/list result and inclusive cursor.

    Page 1: ids w1..w4 (take=100 in request, but mock returns 4)
    Page 2 (cursor=w4): returns w4 (dup), w5, w6 — w4 is a duplicate, w5+w6 are new
    Page 3 (cursor=w6): returns only w6 (dup) — no new ids → loop terminates

    Expected result: 6 unique wallets (w1..w6), no duplicates.
    """
    masumi = make_masumi()

    payment_source_resp = mock_response(200, {
        "data": {
            "PaymentSources": [
                {"id": "src-paged"},
            ]
        }
    })

    page1 = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "w1", "walletVkey": "vkey1", "walletAddress": "addr1", "note": ""},
                {"id": "w2", "walletVkey": "vkey2", "walletAddress": "addr2", "note": ""},
                {"id": "w3", "walletVkey": "vkey3", "walletAddress": "addr3", "note": ""},
                {"id": "w4", "walletVkey": "vkey4", "walletAddress": "addr4", "note": ""},
            ]
        }
    })

    # cursor=w4 (inclusive) → w4 appears again as first item
    page2 = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "w4", "walletVkey": "vkey4", "walletAddress": "addr4", "note": ""},  # dup
                {"id": "w5", "walletVkey": "vkey5", "walletAddress": "addr5", "note": ""},
                {"id": "w6", "walletVkey": "vkey6", "walletAddress": "addr6", "note": ""},
            ]
        }
    })

    # cursor=w6 (inclusive) → only w6, no new ids → terminate
    page3 = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "w6", "walletVkey": "vkey6", "walletAddress": "addr6", "note": ""},  # dup
            ]
        }
    })

    cm, client_mock = make_httpx_client_patch([
        payment_source_resp,
        page1,
        page2,
        page3,
    ])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        result = await list_wallets(masumi)

    # (a) no duplicates
    result_ids = [w["walletVkey"] for w in result]
    assert len(result_ids) == len(set(result_ids)), "Duplicates found in result"

    # (b) loop terminates (test itself terminates — no infinite loop)

    # (c) all 6 unique wallets returned
    assert len(result) == 6
    expected_vkeys = {f"vkey{i}" for i in range(1, 7)}
    assert {w["walletVkey"] for w in result} == expected_vkeys


# ---------------------------------------------------------------------------
# Test 3b: Helper _list_selling_wallets_v2 directly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_selling_wallets_v2_dedup_and_termination():
    """
    Direct test of _list_selling_wallets_v2 with inclusive cursor producing duplicates.

    Verifies:
    - Dedup by id
    - Termination when no new ids
    - All unique wallets in result
    """
    masumi = make_masumi()

    page1 = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "a", "walletVkey": "vkA", "walletAddress": "addrA", "note": ""},
                {"id": "b", "walletVkey": "vkB", "walletAddress": "addrB", "note": ""},
            ]
        }
    })

    # cursor=b (inclusive): b repeats, c is new
    page2 = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "b", "walletVkey": "vkB", "walletAddress": "addrB", "note": ""},  # dup
                {"id": "c", "walletVkey": "vkC", "walletAddress": "addrC", "note": ""},
            ]
        }
    })

    # cursor=c (inclusive): only c → no new ids → stop
    page3 = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "c", "walletVkey": "vkC", "walletAddress": "addrC", "note": ""},  # dup
            ]
        }
    })

    client_mock = AsyncMock()
    client_mock.get = AsyncMock(side_effect=[page1, page2, page3])

    result = await _list_selling_wallets_v2(client_mock, masumi, "src-direct")

    assert len(result) == 3
    assert {w["walletVkey"] for w in result} == {"vkA", "vkB", "vkC"}
    # All share the same source id
    assert all(w["sourceId"] == "src-direct" for w in result)


# ---------------------------------------------------------------------------
# Test 4: take >= 2 invariant — verify take=100 is used (never take=1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_schema_take_100_not_1():
    """
    Verify that /wallet/list requests use take=100 (not take=1 or any smaller value).

    take=1 with an inclusive cursor would yield 0 new items on every page after the
    first — causing premature termination (take=1 → page=[item], cursor=item,
    next_page=[same_item] → no new ids → stop after first item).
    """
    masumi = make_masumi()

    payment_source_resp = mock_response(200, {
        "data": {
            "PaymentSources": [
                {"id": "src-take-check"},
            ]
        }
    })

    wallet_list_resp = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "x1", "walletVkey": "vkX1", "walletAddress": "aX1", "note": ""},
            ]
        }
    })

    # After cursor=x1: returns x1 again (inclusive), no new ids → stop
    wallet_list_cursor_resp = mock_response(200, {
        "data": {"Wallets": [
            {"id": "x1", "walletVkey": "vkX1", "walletAddress": "aX1", "note": ""},
        ]}
    })

    cm, client_mock = make_httpx_client_patch([
        payment_source_resp,
        wallet_list_resp,
        wallet_list_cursor_resp,
    ])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        await list_wallets(masumi)

    # Inspect all calls to client.get
    all_urls = [str(call.args[0]) for call in client_mock.get.call_args_list]
    wallet_list_urls = [u for u in all_urls if "/wallet/list" in u]

    # Every /wallet/list call must have take >= 2 (specifically take=100)
    for url in wallet_list_urls:
        assert "take=100" in url, f"Expected take=100 in URL, got: {url}"
        assert "take=1&" not in url and not url.endswith("take=1"), \
            f"take=1 found in URL: {url}"


# ---------------------------------------------------------------------------
# Test 5: Non-200 from /payment-source → returns []
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_payment_source_non200_returns_empty():
    """Non-200 response from /payment-source → empty list, no exception."""
    masumi = make_masumi()

    error_resp = mock_response(401, {"error": "Unauthorized"})

    cm, _ = make_httpx_client_patch([error_resp])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        result = await list_wallets(masumi)

    assert result == []


@pytest.mark.asyncio
async def test_payment_source_500_returns_empty():
    """500 from /payment-source → empty list."""
    masumi = make_masumi()

    error_resp = mock_response(500, {"error": "Internal Server Error"})

    cm, _ = make_httpx_client_patch([error_resp])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        result = await list_wallets(masumi)

    assert result == []


# ---------------------------------------------------------------------------
# Test 6: Non-200 from /wallet/list → partial result (empty for that source)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_schema_wallet_list_non200_returns_empty_source():
    """Non-200 from /wallet/list → that source contributes no wallets; no crash."""
    masumi = make_masumi()

    payment_source_resp = mock_response(200, {
        "data": {
            "PaymentSources": [
                {"id": "src-ok"},
                {"id": "src-fail"},
            ]
        }
    })

    wallet_ok = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "ok1", "walletVkey": "vkOK", "walletAddress": "addrOK", "note": ""},
            ]
        }
    })

    # Termination page for src-ok (inclusive cursor → only ok1 again)
    wallet_ok_cursor = mock_response(200, {
        "data": {"Wallets": [
            {"id": "ok1", "walletVkey": "vkOK", "walletAddress": "addrOK", "note": ""},
        ]}
    })

    wallet_fail = mock_response(403, {"error": "Forbidden"})

    cm, _ = make_httpx_client_patch([
        payment_source_resp,
        wallet_ok,
        wallet_ok_cursor,
        wallet_fail,
    ])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        result = await list_wallets(masumi)

    # src-ok wallet is present, src-fail contributes nothing
    assert len(result) == 1
    assert result[0]["sourceId"] == "src-ok"


# ---------------------------------------------------------------------------
# Test: Return shape matches existing contract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_return_shape_old_schema():
    """Every returned wallet must have exactly: walletVkey, walletAddress, sourceId, note."""
    masumi = make_masumi()

    payment_source_resp = mock_response(200, {
        "data": {
            "PaymentSources": [
                {
                    "id": "s1",
                    "SellingWallets": [
                        {"walletVkey": "vk", "walletAddress": "addr", "note": "n"},
                    ],
                }
            ]
        }
    })

    cm, _ = make_httpx_client_patch([payment_source_resp])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        result = await list_wallets(masumi)

    assert len(result) == 1
    w = result[0]
    assert set(w.keys()) == {"walletVkey", "walletAddress", "sourceId", "note"}


@pytest.mark.asyncio
async def test_return_shape_new_schema():
    """NEW schema wallets also have exactly the four required keys."""
    masumi = make_masumi()

    payment_source_resp = mock_response(200, {
        "data": {
            "PaymentSources": [{"id": "s2"}]
        }
    })

    wallet_list_resp = mock_response(200, {
        "data": {
            "Wallets": [
                {"id": "w1", "walletVkey": "vk", "walletAddress": "addr", "note": "n",
                 "collectionAddress": "caddr", "LowBalanceSummary": {}},
            ]
        }
    })

    # Termination: cursor=w1 returns w1 (inclusive) → no new ids
    wallet_list_term = mock_response(200, {
        "data": {"Wallets": [
            {"id": "w1", "walletVkey": "vk", "walletAddress": "addr", "note": "n"},
        ]}
    })

    cm, _ = make_httpx_client_patch([payment_source_resp, wallet_list_resp, wallet_list_term])
    with patch("kodosumi.service.expose.registry.HTTPXClient", return_value=cm):
        result = await list_wallets(masumi)

    assert len(result) == 1
    w = result[0]
    assert set(w.keys()) == {"walletVkey", "walletAddress", "sourceId", "note"}
