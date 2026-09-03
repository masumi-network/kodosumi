"""
Pricing translation between flow YAML and the Masumi registry.

A registration carries its price in the shape the payment source
expects: a V1 entry prices the agent once, a V2 entry prices every
supported payment source on its own. These functions move a price
between the operator's YAML and either shape, and refuse the values the
payment node would reject with an opaque 400.
"""

from typing import Any, Dict, List

from kodosumi.service.expose.currency import (CURRENCY_UNITS,
                                              human_to_base_amount)

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
