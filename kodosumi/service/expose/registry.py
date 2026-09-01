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

logger = logging.getLogger(__name__)

# Currency mapping: human-readable → hex unit on-chain
CURRENCY_UNITS = {
    "USDM": {
        "Preprod": "16a55b2a349361ff88c03788f93e1e966e5d689605d044fef722ddde0014df10745553444d",
        "Mainnet": "c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad0014df105553444d",
    },
    "ADA": {
        "Preprod": "",
        "Mainnet": "",
    },
}

# All currencies have 6 decimal places
CURRENCY_DECIMALS = 6

# Masumi payment source types. A payment source is the deployed escrow
# contract a selling wallet belongs to, and it decides the registration
# shape: V1 entries carry top-level AgentPricing, V2 entries carry a
# supportedPaymentSources list that prices every source on its own.
PAYMENT_SOURCE_TYPE_V1 = "Web3CardanoV1"
PAYMENT_SOURCE_TYPE_V2 = "Web3CardanoV2"

# Index into supportedPaymentSources that Kodosumi registers and pays with.
# Kodosumi advertises exactly one source per agent, so the index is always 0.
DEFAULT_SUPPORTED_PAYMENT_SOURCE_INDEX = 0

# Bounds the payment node enforces on a V2 registration. Checking them here
# turns an opaque 400 from the node into a message that names the flow YAML
# field the operator has to correct.
MIN_FIXED_PRICING_ENTRIES = 1
MAX_FIXED_PRICING_ENTRIES = 5
# The node bounds the amount string at 19 characters and parses it into a
# Postgres bigint, so leading zeros count and the value has a ceiling.
MAX_ATOMIC_AMOUNT_DIGITS = 19
MAX_ATOMIC_AMOUNT = 9223372036854775807

# Page size of the /wallet endpoint. The node caps `take` at 100 and its
# cursor is inclusive, so a page repeats the row the cursor names.
WALLET_PAGE_SIZE = 100
MAX_WALLET_PAGES = 50


def human_to_base_amount(amount: float) -> str:
    """Convert human-readable amount (e.g. 0.01) to base units string (e.g. '10000')."""
    base = round(amount * (10 ** CURRENCY_DECIMALS))
    return str(base)


def base_to_human_amount(base_amount: str) -> float:
    """Convert base units string (e.g. '10000') to human-readable amount (e.g. 0.01)."""
    return int(base_amount) / (10 ** CURRENCY_DECIMALS)


def unit_to_currency(unit: str) -> str:
    """Map hex unit string back to human-readable currency name."""
    for currency, networks in CURRENCY_UNITS.items():
        for network, hex_unit in networks.items():
            if hex_unit == unit:
                return currency
    return "ADA" if unit == "" else "unknown"


def pricing_yaml_to_registry(pricing_yaml: Any, network: str) -> Dict:
    """
    Convert Kodosumi YAML pricing format to Masumi Registry API format.

    YAML format:
        agentPricing:
          - pricingType: Fixed
            fixedPricing:
              - amount: "10000"
                unit: "16a55b2a..."

    Registry format:
        {"pricingType": "Fixed", "Pricing": [{"amount": "10000", "unit": "16a55b2a..."}]}

    Raises ValueError when the YAML is not one of the shapes above. The
    metadata is hand edited, so a mapping or a scalar reaches this function
    and must become a message the operator can act on, not a KeyError.
    """
    if not pricing_yaml:
        return {"pricingType": "Free"}

    # A single mapping is the shape operators write most often by mistake.
    if isinstance(pricing_yaml, dict):
        pricing_yaml = [pricing_yaml]
    if not isinstance(pricing_yaml, list) or not isinstance(
            pricing_yaml[0], dict):
        raise ValueError(
            "agentPricing must be a list of pricing entries, for example: "
            "agentPricing:\n  - pricingType: Free"
        )

    first = pricing_yaml[0]
    pricing_type = first.get("pricingType", "Free")

    if pricing_type == "Free":
        return {"pricingType": "Free"}

    fixed_pricing = first.get("fixedPricing") or []
    if not isinstance(fixed_pricing, list):
        raise ValueError(
            "agentPricing[0].fixedPricing must be a list of "
            "{amount, unit} entries")
    registry_pricing = []
    for p in fixed_pricing:
        if not isinstance(p, dict):
            raise ValueError(
                "Every agentPricing[0].fixedPricing entry must be a mapping "
                "with an amount and a unit")
        unit = p.get("unit", "")
        # Convert "lovelace" to empty string for registry
        if unit == "lovelace":
            unit = ""
        registry_pricing.append({
            "amount": str(p.get("amount", "0")),
            "unit": unit,
        })

    return {
        "pricingType": "Fixed",
        "Pricing": registry_pricing,
    }


def pricing_to_yaml_format(pricing_type: str, amount: float, currency: str, network: str) -> List[Dict]:
    """
    Convert UI pricing input to Kodosumi YAML format.

    Returns list suitable for agentPricing in meta YAML.
    """
    if pricing_type == "Free":
        return [{"pricingType": "Free"}]

    unit_hex = CURRENCY_UNITS.get(currency, {}).get(network, "")
    base_amount = human_to_base_amount(amount)

    return [{
        "pricingType": "Fixed",
        "fixedPricing": [{
            "amount": base_amount,
            "unit": unit_hex,
        }],
    }]


def _atomic_amount(amount: Any) -> str:
    """
    Render one price as the atomic amount string the payment node accepts.

    The node takes digits only, at most 19 characters of them, and rejects
    zero. A missing amount used to default to "0" and was refused on chain
    with an error that never named the flow YAML.

    The result is normalised, so leading zeros written in the YAML cannot
    push an otherwise valid amount past the node's 19 character bound.
    """
    text = str(amount if amount is not None else "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(
            f"Pricing amount must be a positive whole number of base units, "
            f"got '{amount}'."
        )
    value = int(text)
    if value > MAX_ATOMIC_AMOUNT:
        raise ValueError(
            f"Pricing amount is above the largest amount the payment node "
            f"stores ({MAX_ATOMIC_AMOUNT}): '{amount}'."
        )
    normalised = str(value)
    if len(normalised) > MAX_ATOMIC_AMOUNT_DIGITS:
        raise ValueError(
            f"Pricing amount has more than {MAX_ATOMIC_AMOUNT_DIGITS} digits: "
            f"'{amount}'."
        )
    return normalised


def registry_pricing_to_supported_sources(
    registry_pricing: Dict,
    network: str,
    smart_contract_address: str,
) -> List[Dict]:
    """
    Convert V1 registry pricing into the V2 supportedPaymentSources list.

    V2 registrations reject the top-level AgentPricing field. Each entry in
    supportedPaymentSources names one escrow contract and owns its price.
    Kodosumi advertises the single contract the selling wallet belongs to.

    Registry format (V1):
        {"pricingType": "Fixed", "Pricing": [{"amount": "10000", "unit": ""}]}

    Supported source format (V2):
        [{"chain": "Cardano", "network": "Preprod",
          "paymentSourceType": "Web3CardanoV2", "address": "addr_test1...",
          "pricing": {"pricingType": "Fixed",
                      "fixed": [{"asset": "", "amount": "10000"}]}}]
    """
    if not smart_contract_address:
        raise ValueError(
            "V2 registration requires the smart contract address of the "
            "selling wallet payment source"
        )

    pricing_type = registry_pricing.get("pricingType", "Free")
    if pricing_type == "Fixed":
        pricing: Dict[str, Any] = {
            "pricingType": "Fixed",
            "fixed": [
                {
                    "asset": price.get("unit", ""),
                    "amount": _atomic_amount(price.get("amount")),
                }
                for price in registry_pricing.get("Pricing") or []
            ],
        }
        entry_count = len(pricing["fixed"])
        if not (MIN_FIXED_PRICING_ENTRIES <= entry_count
                <= MAX_FIXED_PRICING_ENTRIES):
            raise ValueError(
                f"Fixed pricing needs between {MIN_FIXED_PRICING_ENTRIES} and "
                f"{MAX_FIXED_PRICING_ENTRIES} priced assets, got "
                f"{entry_count}. Add fixedPricing entries to agentPricing."
            )
    else:
        pricing = {"pricingType": pricing_type}

    return [{
        "chain": "Cardano",
        "network": network,
        "paymentSourceType": PAYMENT_SOURCE_TYPE_V2,
        "address": smart_contract_address,
        "pricing": pricing,
    }]


async def _list_source_selling_wallets(
    client: Any, masumi: MasumiConfig, headers: Dict, source_id: str
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
        if len(page) < WALLET_PAGE_SIZE or not fresh:
            return wallets
        cursor_id = page[-1].get("id") or ""
        if not cursor_id:
            return wallets
    logger.warning(
        "Stopped listing wallets of payment source %s after %s pages",
        source_id, MAX_WALLET_PAGES,
    )
    return wallets


async def list_wallets(masumi: MasumiConfig) -> List[Dict]:
    """
    List selling wallets from Masumi Payment API.

    Returns list of dicts with walletVkey, walletAddress, and source info.
    Each entry carries the paymentSourceType and smartContractAddress of
    its payment source, because the wallet selects the registration
    version: a Web3CardanoV2 wallet registers a V2 agent.
    """
    url = f"{masumi.base_url}/payment-source?network={masumi.registry_network}"
    headers = {"accept": "application/json", "token": masumi.token}

    try:
        async with HTTPXClient() as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning("Failed to list wallets: %s", resp.text)
                return []
            data = resp.json().get("data", {})
            sources = [
                source for source in data.get("PaymentSources", [])
                if not source.get("network")
                or source.get("network") == masumi.registry_network
            ]
            # Sources without embedded wallets cost one request each. Run
            # them together: the register and migrate dialogs both wait on
            # this call before they can show anything.
            missing = [index for index, source in enumerate(sources)
                       if not source.get("SellingWallets")]
            fetched = dict(zip(missing, await asyncio.gather(*[
                _list_source_selling_wallets(
                    client, masumi, headers, sources[index].get("id", ""))
                for index in missing
            ])))

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
        return []


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
) -> Optional[Dict]:
    """
    Check registration status from Masumi Registry.

    Looks up by registrationId, agentIdentifier, or searchQuery.
    Pass payment_source_type for a V2 registration: the registry list
    endpoint falls back to Web3CardanoV1 when no type is given, so a V2
    registration never appears in the paginated search without it.
    Returns the matching registration dict or None.
    """
    headers = {"accept": "application/json", "token": masumi.token}

    if agent_identifier:
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
                    if registration_id and asset.get("id") == registration_id:
                        return asset
                    if agent_identifier and asset.get("agentIdentifier") == agent_identifier:
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
