"""
MIP-004: Input Hash Calculation.

The input hash provides a verifiable fingerprint of job inputs using
SHA-256 over a canonical JSON representation.

Algorithm (3 Steps per MIP-004):
1. Canonical JSON Serialization (RFC 8785)
2. Pre-image Assembly: identifier_from_purchaser + ";" + canonical_json
3. SHA-256 Hash (lowercase hex)

Note: For strict RFC 8785 compliance, install `canonicaljson`:
    pip install canonicaljson

If not available, falls back to Python's json with sort_keys=True,
which works for most common cases.
"""

import hashlib
import json
from typing import Any, Dict, Optional

# Try to import canonicaljson for strict RFC 8785 compliance
try:
    import canonicaljson

    HAS_CANONICALJSON = True
except ImportError:
    HAS_CANONICALJSON = False


def _canonical_json(data: Dict[str, Any]) -> str:
    """
    Convert dict to canonical JSON string.

    Uses RFC 8785 (JSON Canonicalization Scheme) if canonicaljson
    is available, otherwise falls back to sorted JSON.
    """
    if HAS_CANONICALJSON:
        # Strict RFC 8785 compliance
        return canonicaljson.encode_canonical_json(data).decode("utf-8")
    else:
        # Fallback: sorted keys, no whitespace
        # Note: This is not 100% RFC 8785 compliant but works for most cases
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def create_input_hash(
    input_data: Optional[Dict[str, Any]], identifier_from_purchaser: str
) -> str:
    """
    Create MIP-004 compliant input hash.

    Args:
        input_data: Job input data dict (or None/empty)
        identifier_from_purchaser: Customer-defined job identifier

    Returns:
        Lowercase hexadecimal SHA-256 hash string

    Example:
        >>> create_input_hash({"query": "hello"}, "order-123")
        'a1b2c3...'
    """
    # Step 1: Canonical JSON serialization
    if input_data is None:
        input_data = {}
    canonical_json_string = _canonical_json(input_data)

    # Step 2: Pre-image assembly with semicolon delimiter
    # The semicolon prevents concatenation ambiguity attacks
    string_to_hash = f"{identifier_from_purchaser};{canonical_json_string}"

    # Step 3: SHA-256 hash
    return hashlib.sha256(string_to_hash.encode("utf-8")).hexdigest()


def verify_input_hash(
    input_data: Optional[Dict[str, Any]],
    identifier_from_purchaser: str,
    expected_hash: str,
) -> bool:
    """
    Verify an input hash matches expected value.

    Args:
        input_data: Job input data dict
        identifier_from_purchaser: Customer-defined job identifier
        expected_hash: Expected hash to compare against

    Returns:
        True if hash matches, False otherwise
    """
    computed = create_input_hash(input_data, identifier_from_purchaser)
    return computed == expected_hash.lower()
