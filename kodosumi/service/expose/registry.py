"""
Masumi Registry integration for expose management.

Handles agent registration, status polling, and wallet listing
via the Masumi Payment API.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import yaml

from kodosumi.config import MasumiConfig
from kodosumi.helper import HTTPXClient
from kodosumi.service.expose.currency import (CURRENCY_DECIMALS,  # noqa: F401
                                              CURRENCY_UNITS,
                                              base_to_human_amount,
                                              human_to_base_amount,
                                              unit_to_currency)
from kodosumi.service.expose.pricing import (  # noqa: F401
    DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX, MAX_ATOMIC_AMOUNT,
    MAX_ATOMIC_AMOUNT_DIGITS, MAX_FIXED_PRICING_ENTRIES,
    MIN_FIXED_PRICING_ENTRIES, PAYMENT_SOURCE_TYPE_V1,
    PAYMENT_SOURCE_TYPE_V2, pricing_to_yaml_format,
    pricing_yaml_to_registry, registry_pricing_to_supported_sources)
from kodosumi.service.expose.wallet_inventory import (WalletReport,
                                                      record_problem)

logger = logging.getLogger(__name__)


# Page size of the /wallet endpoint. The node caps `take` at 100 and its
# cursor is inclusive, so a page repeats the row the cursor names.
WALLET_PAGE_SIZE = 100
MAX_WALLET_PAGES = 50
MAX_CONCURRENT_WALLET_REQUESTS = 20
PAYMENT_SOURCE_PAGE_SIZE = 100
MAX_PAYMENT_SOURCE_PAGES = 50



async def _list_source_selling_wallets(
    client: Any,
    masumi: MasumiConfig,
    headers: Dict,
    source_id: str,
    require_complete: bool = False,
    report: Optional[WalletReport] = None,
) -> List[Dict]:
    """
    Read selling wallets of one payment source from the /wallet endpoint.

    Payment nodes used to embed SellingWallets in the /payment-source
    response and newer ones do not, so this is the fallback for sources
    that come back without them.
    """
    if not source_id:
        return []
    wallets: List[Dict] = []
    seen_ids = set()
    cursor_id = ""
    for _ in range(MAX_WALLET_PAGES):
        url = (
            f"{masumi.base_url}/wallet"
            f"?paymentSourceId={source_id}&walletType=Selling"
            f"&take={WALLET_PAGE_SIZE}"
        )
        if cursor_id:
            url += f"&cursorId={cursor_id}"
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            if require_complete:
                raise RuntimeError(
                    f"Could not list every wallet of payment source "
                    f"{source_id}: HTTP {resp.status_code}")
            record_problem(
                report,
                f"GET /wallet for payment source {source_id} answered "
                f"HTTP {resp.status_code}")
            logger.warning(
                "Failed to list wallets of payment source %s: %s",
                source_id, resp.text,
            )
            return wallets
        page = resp.json().get("data", {}).get("Wallets", [])
        # The node's cursor is inclusive, so a page repeats the row the
        # cursor names. Without the id check that row would be listed twice.
        # A row without an id cannot be matched, and dropping every one of
        # them after the first would lose wallets, so they are all kept.
        fresh = [w for w in page
                 if not w.get("id") or w.get("id") not in seen_ids]
        seen_ids.update(w["id"] for w in fresh if w.get("id"))
        wallets.extend(fresh)
        if len(page) < WALLET_PAGE_SIZE:
            return wallets
        cursor_id = page[-1].get("id") or ""
        if not fresh or not cursor_id:
            if require_complete:
                raise RuntimeError(
                    f"Could not completely paginate wallets of payment "
                    f"source {source_id}")
            # The list stops here and is not the whole list, so a wallet
            # can be missing from it. Say so, or the caller reads the
            # absence as proof that no such wallet exists.
            record_problem(
                report,
                f"the wallet list of payment source {source_id} could not "
                f"be paged past {len(wallets)} row(s)")
            return wallets
    if require_complete:
        raise RuntimeError(
            f"Could not completely paginate wallets of payment source "
            f"{source_id} within {MAX_WALLET_PAGES} pages")
    record_problem(
        report,
        f"listing the wallets of payment source {source_id} stopped after "
        f"{MAX_WALLET_PAGES} pages")
    logger.warning(
        "Stopped listing wallets of payment source %s after %s pages",
        source_id, MAX_WALLET_PAGES,
    )
    return wallets


async def _list_payment_sources(
    client: HTTPXClient,
    masumi: MasumiConfig,
    headers: dict,
    require_complete: bool = False,
    report: Optional[WalletReport] = None,
) -> List[Dict]:
    """List all payment sources across the node's inclusive cursor."""
    sources: List[Dict] = []
    seen_ids = set()
    cursor_id = ""
    for _ in range(MAX_PAYMENT_SOURCE_PAGES):
        url = (
            f"{masumi.base_url}/payment-source"
            f"?network={masumi.registry_network}"
            f"&take={PAYMENT_SOURCE_PAGE_SIZE}"
        )
        if cursor_id:
            url += f"&cursorId={cursor_id}"
        try:
            resp = await client.get(url, headers=headers)
        except Exception as error:
            if require_complete:
                raise RuntimeError(
                    "Could not load the complete payment source list"
                ) from error
            record_problem(
                report, f"GET /payment-source failed: {error}")
            logger.warning(
                "Stopped listing payment sources after %s entries: %s",
                len(sources), error,
            )
            return sources
        if resp.status_code != 200:
            if require_complete:
                raise RuntimeError(
                    "Could not load the complete payment source list: "
                    f"HTTP {resp.status_code}")
            record_problem(
                report,
                f"GET /payment-source answered HTTP {resp.status_code}")
            logger.warning("Failed to list payment sources: %s", resp.text)
            return sources
        page = resp.json().get("data", {}).get("PaymentSources", [])
        fresh = [source for source in page
                 if not source.get("id")
                 or source.get("id") not in seen_ids]
        seen_ids.update(
            source["id"] for source in fresh if source.get("id"))
        sources.extend(fresh)
        if len(page) < PAYMENT_SOURCE_PAGE_SIZE:
            return sources
        cursor_id = page[-1].get("id") or ""
        if not fresh or not cursor_id:
            if require_complete:
                raise RuntimeError(
                    "Could not completely paginate payment sources")
            record_problem(
                report,
                f"the payment source list could not be paged past "
                f"{len(sources)} row(s)")
            return sources
    if require_complete:
        raise RuntimeError(
            "Could not load the complete payment source list within "
            f"{MAX_PAYMENT_SOURCE_PAGES} pages")
    record_problem(
        report,
        f"listing the payment sources stopped after "
        f"{MAX_PAYMENT_SOURCE_PAGES} pages")
    logger.warning(
        "Stopped listing payment sources after %s pages",
        MAX_PAYMENT_SOURCE_PAGES,
    )
    return sources


async def list_wallets(
    masumi: MasumiConfig,
    require_complete: bool = False,
    report: Optional[WalletReport] = None,
) -> List[Dict]:
    """
    List selling wallets from Masumi Payment API.

    Returns list of dicts with walletVkey, walletAddress, and source info.
    Each entry carries the paymentSourceType and smartContractAddress of
    its payment source, because the wallet selects the registration
    version: a Web3CardanoV2 wallet registers a V2 agent.

    Pass a report to learn why an empty list is empty. The node answers a
    token that may not read this network with a normal empty 200, so the
    result on its own cannot tell a missing wallet from a missing
    permission.
    """
    headers = {"accept": "application/json", "token": masumi.token}

    try:
        async with HTTPXClient() as client:
            payment_sources = await _list_payment_sources(
                client, masumi, headers, require_complete, report)
            sources = [
                source for source in payment_sources
                if not source.get("network")
                or source.get("network") == masumi.registry_network
            ]
            if report is not None:
                report.source_count = len(payment_sources)
                report.matched_count = len(sources)
                report.networks = sorted(
                    {source.get("network") for source in payment_sources
                     if source.get("network")})
            missing = [index for index, source in enumerate(sources)
                       if not source.get("SellingWallets")]
            semaphore = asyncio.Semaphore(
                MAX_CONCURRENT_WALLET_REQUESTS)

            async def fetch_wallets(index: int) -> List[Dict]:
                async with semaphore:
                    return await _list_source_selling_wallets(
                        client,
                        masumi,
                        headers,
                        sources[index].get("id", ""),
                        require_complete,
                        report,
                    )

            results = await asyncio.gather(
                *(fetch_wallets(index) for index in missing),
                return_exceptions=True,
            )
            fetched = {}
            for index, result in zip(missing, results):
                if isinstance(result, BaseException):
                    if require_complete:
                        raise RuntimeError(
                            "Could not load the complete selling wallet "
                            "inventory") from result
                    record_problem(
                        report,
                        f"GET /wallet for payment source "
                        f"{sources[index].get('id', '')} failed: {result}")
                    logger.warning(
                        "Failed to list wallets of payment source %s: %s",
                        sources[index].get("id", ""), result)
                    continue
                fetched[index] = result

            wallets = []
            for index, source in enumerate(sources):
                source_network = source.get("network")
                source_wallets = (
                    source.get("SellingWallets") or fetched.get(index) or [])
                for wallet in source_wallets:
                    wallets.append({
                        "walletVkey": wallet.get("walletVkey", ""),
                        "walletAddress": wallet.get("walletAddress", ""),
                        "sourceId": source.get("id", ""),
                        "note": wallet.get("note") or "",
                        "network": source_network or "",
                        "paymentSourceType": source.get("paymentSourceType", ""),
                        "smartContractAddress": source.get(
                            "smartContractAddress", ""),
                    })
            return wallets
    except Exception as e:
        logger.error("Error listing wallets: %s", e)
        if require_complete:
            raise
        record_problem(report, str(e))
        return []


def select_wallet(
    wallets: List[Dict], wallet_vkey: str
) -> Optional[Dict]:
    """Pick the wallet a request names, or refuse an ambiguous key.

    The wallet dropdown carries the verification key as its only value, so
    a key that belongs to two payment sources arrives here indistinguishable.
    The wallet decides the rail of the mint, and a mint on the wrong escrow
    contract cannot be taken back, so an ambiguous key is refused rather
    than resolved to whichever source the node listed first.
    """
    matches = [w for w in wallets if w.get("walletVkey") == wallet_vkey]
    if not matches:
        return None
    rails = {w.get("paymentSourceType") or PAYMENT_SOURCE_TYPE_V1
             for w in matches}
    if len(rails) > 1:
        raise ValueError(
            f"Wallet '{wallet_vkey[:8]}...' belongs to more than one payment "
            f"source ({', '.join(sorted(rails))}). Kodosumi cannot tell which "
            "one you picked. Use a selling wallet that belongs to a single "
            "payment source."
        )
    return matches[0]


async def register_agent(
    masumi: MasumiConfig,
    name: str,
    description: str,
    api_base_url: str,
    tags: List[str],
    pricing: Optional[Dict] = None,
    author: Optional[Dict] = None,
    capability: Optional[Dict] = None,
    legal: Optional[Dict] = None,
    wallet_vkey: str = "",
    supported_payment_sources: Optional[List[Dict]] = None,
) -> Dict:
    """
    Register an agent on the Masumi on-chain registry.

    Pass supported_payment_sources to register against a Web3CardanoV2
    payment source. The node rejects a V2 registration that also sets
    AgentPricing, and a V1 registration that sets supportedPaymentSources,
    so exactly one of the two is sent.

    Returns the registration response dict.
    """
    if supported_payment_sources is None and pricing is None:
        raise ValueError(
            "register_agent needs either pricing for a Web3CardanoV1 "
            "registration or supported_payment_sources for a V2 one")
    url = f"{masumi.base_url}/registry"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "token": masumi.token,
    }

    body = {
        "network": masumi.registry_network,
        "sellingWalletVkey": wallet_vkey,
        "name": name,
        "description": description or "",
        "apiBaseUrl": api_base_url,
        "Tags": tags or [],
        "ExampleOutputs": [],
        "Capability": capability or {"name": "", "version": "1.0"},
        "Author": author or {"name": "", "contactEmail": "", "organization": ""},
        "Legal": legal or {},
    }

    # `is not None`, not truthiness: an empty list is a V2 registration with
    # no priced source, and posting AgentPricing instead makes the node
    # complain about a field the caller never meant to send.
    if supported_payment_sources is not None:
        body["supportedPaymentSources"] = supported_payment_sources
    else:
        body["AgentPricing"] = pricing

    async with HTTPXClient() as client:
        resp = await client.post(url, headers=headers, json=body)
        result = resp.json()
        if resp.status_code != 200 and resp.status_code != 201:
            error = result.get("error", result.get("message", resp.text))
            raise RuntimeError(f"Registration failed: {error}")
        return result.get("data", result)


async def get_registration_status(
    masumi: MasumiConfig,
    registration_id: Optional[str] = None,
    agent_identifier: Optional[str] = None,
    search_query: Optional[str] = None,
    payment_source_type: Optional[str] = None,
    registry_row_only: bool = False,
) -> Optional[Dict]:
    """
    Check registration status from Masumi Registry.

    Looks up by registrationId, agentIdentifier, or searchQuery.
    Pass payment_source_type for a V2 registration: the registry list
    endpoint falls back to Web3CardanoV1 when no type is given, so a V2
    registration never appears in the paginated search without it.
    Set registry_row_only when the request lifecycle state matters. The
    direct agent lookup can only report whether the NFT exists.
    Returns the matching registration dict or None.
    """
    headers = {"accept": "application/json", "token": masumi.token}

    if agent_identifier and not registration_id and not registry_row_only:
        url = f"{masumi.base_url}/registry/agent-identifier?network={masumi.registry_network}&agentIdentifier={agent_identifier}"
        try:
            async with HTTPXClient() as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    if data.get("agentIdentifier"):
                        # /agent-identifier returns {agentIdentifier, Metadata, ...}
                        # but has NO state or id field. If found here, agent is on-chain = confirmed.
                        meta = data.get("Metadata", {})
                        return {
                            "state": "RegistrationConfirmed",
                            "agentIdentifier": data["agentIdentifier"],
                            "name": meta.get("name"),
                            "description": meta.get("description"),
                            "apiBaseUrl": meta.get("apiBaseUrl"),
                            "AgentPricing": meta.get("AgentPricing"),
                            # Null for V1 metadata, set for V2 entries whose
                            # price lives inside the advertised sources.
                            "supportedPaymentSources": meta.get(
                                "supportedPaymentSources"),
                            "Tags": meta.get("Tags", []),
                        }
        except Exception as e:
            logger.error("Error checking agent-identifier: %s", e)

    # Fallback: paginated search in registry list (using cursorId)
    limit = 100
    cursor_id = None
    try:
        async with HTTPXClient() as client:
            while True:
                url = (
                    f"{masumi.base_url}/registry"
                    f"?network={masumi.registry_network}"
                    f"&limit={limit}"
                )
                if payment_source_type:
                    url += f"&filterPaymentSourceType={payment_source_type}"
                if cursor_id:
                    url += f"&cursorId={cursor_id}"
                if search_query:
                    url += f"&searchQuery={search_query}"
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    break
                assets = resp.json().get("data", {}).get("Assets", [])
                if not assets:
                    break
                for asset in assets:
                    id_matches = (
                        not registration_id
                        or asset.get("id") == registration_id
                    )
                    agent_matches = (
                        not agent_identifier
                        or asset.get("agentIdentifier") == agent_identifier
                    )
                    if ((registration_id or agent_identifier)
                            and id_matches and agent_matches):
                        return asset
                if search_query and assets:
                    return assets[0]
                if len(assets) < limit:
                    break
                cursor_id = assets[-1].get("id")
                if not cursor_id:
                    break
    except Exception as e:
        logger.error("Error checking registry: %s", e)

    return None


async def deregister_agent(
    masumi: MasumiConfig,
    agent_identifier: str,
) -> Dict:
    """Deregister an agent from the on-chain registry."""
    url = f"{masumi.base_url}/registry/deregister"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "token": masumi.token,
    }
    body = {
        "network": masumi.registry_network,
        "agentIdentifier": agent_identifier,
    }

    async with HTTPXClient() as client:
        resp = await client.post(url, headers=headers, json=body)
        result = resp.json()
        if resp.status_code != 200:
            error = result.get("error", result.get("message", resp.text))
            raise RuntimeError(f"Deregistration failed: {error}")
        return result.get("data", result)


def update_meta_yaml_field(yaml_str: str, field: str, value: Any) -> str:
    """
    Update or add a field in a meta YAML string.

    Handles replacing commented-out sections and adding new fields.
    Preserves comments for unrelated sections.
    """
    parsed = yaml.safe_load(yaml_str) if yaml_str else {}
    if not isinstance(parsed, dict):
        parsed = {}

    parsed[field] = value

    # Remove commented version of the field if present
    lines = yaml_str.split("\n") if yaml_str else []
    cleaned_lines = []
    skip_commented_block = False

    for line in lines:
        stripped = line.lstrip("# ").strip()
        # Check if this starts a commented-out block for our field
        if line.startswith("#") and stripped.startswith(f"{field}:"):
            skip_commented_block = True
            continue
        if skip_commented_block:
            if line.startswith("#") and (line.startswith("#  ") or line.startswith("#   ") or stripped.startswith("- ") or stripped.startswith("  ")):
                continue
            skip_commented_block = False
        cleaned_lines.append(line)

    # Re-serialize with updated field
    cleaned_yaml = "\n".join(cleaned_lines)
    cleaned_parsed = yaml.safe_load(cleaned_yaml) if cleaned_yaml.strip() else {}
    if not isinstance(cleaned_parsed, dict):
        cleaned_parsed = {}

    cleaned_parsed[field] = value

    return yaml.dump(
        cleaned_parsed,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
