"""Currency units a Masumi price is expressed in.

An amount travels in base units on chain and in human units in the flow
YAML, and the asset is named by a hex unit string that differs per
network. Keeping the mapping here leaves the registry client with one
job, reading and writing registrations.
"""

from typing import Dict

# Currency mapping: human-readable → hex unit on-chain
CURRENCY_UNITS: Dict[str, Dict[str, str]] = {
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
