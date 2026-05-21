#!/usr/bin/env python3
"""
Test script for Sumi protocol agents.

Usage:
    python scripts/test_sumi_agent.py prime
    python scripts/test_sumi_agent.py facebook-page-analysis/analyze
    python scripts/test_sumi_agent.py facebook-page-analysis/analyze --purchase
"""

import argparse
import json
import secrets
import sys
import time
from pprint import pprint

import httpx

# Configuration
BASE_URL = "https://panel-hermes.kodosumi.io"
MASUMI_PURCHASE_URL = "https://payment.masumi.network/api/v1/purchase/"
MASUMI_TOKEN = "iofsnaiojdoiewqajdriknjonasfoinasd"


def get_client():
    return httpx.Client(base_url=BASE_URL, follow_redirects=True, timeout=60.0)


def check_availability(client, endpoint: str) -> bool:
    """Check if the agent is available."""
    resp = client.get(f"/sumi/{endpoint}/availability")
    print(f"\n=== Availability: {endpoint} ===")
    print(f"Status: {resp.status_code}")
    data = resp.json()
    pprint(data)
    return data.get("status") == "available"


def get_input_schema(client, endpoint: str) -> dict:
    """Fetch input schema for the endpoint."""
    resp = client.get(f"/sumi/{endpoint}/input_schema")
    print(f"\n=== Input Schema: {endpoint} ===")
    print(f"Status: {resp.status_code}")
    data = resp.json()
    pprint(data)
    return data


def build_input_data(schema: dict, custom_inputs: dict = None) -> dict:
    """Build input_data from schema with defaults or custom values."""
    input_data = {}
    custom_inputs = custom_inputs or {}

    for field in schema.get("input_data", []):
        field_id = field["id"]
        field_type = field["type"]
        field_name = field.get("name", field_id)
        placeholder = field.get("data", {}).get("placeholder", "") if field.get("data") else ""
        validations = field.get("validations", []) or []
        is_optional = any(v.get("validation") == "optional" for v in validations)

        # Use custom input if provided
        if field_id in custom_inputs:
            input_data[field_id] = custom_inputs[field_id]
            continue

        # Skip optional fields without placeholder
        if is_optional and not placeholder:
            continue

        # Use placeholder as default value
        if placeholder:
            input_data[field_id] = placeholder
        elif not is_optional:
            # Required field without placeholder - use type-appropriate default
            if field_type == "number":
                input_data[field_id] = "10"
            elif field_type == "text":
                input_data[field_id] = "test"
            elif field_type == "textarea":
                input_data[field_id] = "test query"

    return input_data


def start_job(client, endpoint: str, input_data: dict) -> dict:
    """Start a job with the given input data."""
    identifier = secrets.token_hex(13)

    print(f"\n=== Starting Job: {endpoint} ===")
    print(f"Identifier: {identifier}")
    print(f"Input Data:")
    pprint(input_data)

    resp = client.post(f"/sumi/{endpoint}/start_job", json={
        "identifier_from_purchaser": identifier,
        "input_data": input_data
    })

    print(f"\nResponse Status: {resp.status_code}")
    result = resp.json()
    pprint(result)

    return result


def execute_purchase(result: dict) -> dict:
    """Execute purchase via Masumi API."""
    print(f"\n=== Executing Purchase ===")

    if not result.get("blockchainIdentifier"):
        print("ERROR: No blockchainIdentifier - payment not required or not initialized")
        return None

    purchase_data = {
        "identifierFromPurchaser": result.get("identifierFromPurchaser"),
        "network": "Preprod",
        "sellerVkey": result.get("sellerVKey"),
        "blockchainIdentifier": result.get("blockchainIdentifier"),
        "payByTime": str(result.get("payByTime")),
        "submitResultTime": str(result.get("submitResultTime")),
        "unlockTime": str(result.get("unlockTime")),
        "externalDisputeUnlockTime": str(result.get("externalDisputeUnlockTime")),
        "agentIdentifier": result.get("agentIdentifier"),
        "inputHash": result.get("input_hash"),
    }

    print("Purchase Data:")
    pprint(purchase_data)

    resp = httpx.post(
        MASUMI_PURCHASE_URL,
        headers={
            "accept": "application/json",
            "token": MASUMI_TOKEN,
            "Content-Type": "application/json"
        },
        json=purchase_data,
        timeout=30.0
    )

    print(f"\nPurchase Status: {resp.status_code}")
    purchase_result = resp.json()
    pprint(purchase_result)

    return purchase_result


def check_job_status(client, endpoint: str, job_id: str) -> dict:
    """Check job status."""
    print(f"\n=== Job Status: {job_id} ===")

    resp = client.get(f"/sumi/{endpoint}/status/{job_id}")
    print(f"Status: {resp.status_code}")
    result = resp.json()
    pprint(result)

    return result


def poll_job_status(client, endpoint: str, job_id: str, interval: float = 5.0, max_attempts: int = 60) -> dict:
    """Poll job status until completion."""
    print(f"\n=== Polling Job Status: {job_id} ===")

    for i in range(max_attempts):
        resp = client.get(f"/sumi/{endpoint}/status/{job_id}")
        result = resp.json()
        status = result.get("status")

        print(f"[{i+1}/{max_attempts}] Status: {status}")

        if status in ("completed", "failed"):
            print("\n=== Final Result ===")
            pprint(result)
            return result

        time.sleep(interval)

    print("Max attempts reached")
    return result


# Preset configurations for known agents
AGENT_PRESETS = {
    "prime": {
        "start": "0",
        "end": "100000",
        "tasks": "20"
    },
    "facebook-page-analysis/analyze": {
        "facebook_url": "https://www.facebook.com/BMW/",
        "user_query": "What content strategy is this page using?",
        "max_posts": "10"
    }
}


def main():
    parser = argparse.ArgumentParser(description="Test Sumi protocol agents")
    parser.add_argument("endpoint", help="Agent endpoint (e.g., 'prime' or 'facebook-page-analysis/analyze')")
    parser.add_argument("--purchase", action="store_true", help="Execute purchase after start_job")
    parser.add_argument("--poll", action="store_true", help="Poll for job completion")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Polling interval in seconds")
    parser.add_argument("--custom-input", type=str, help="Custom input JSON (overrides presets)")
    parser.add_argument("--base-url", type=str, default=BASE_URL, help="Base URL for the panel")

    args = parser.parse_args()

    # Use provided base URL or default
    base_url = args.base_url

    client = httpx.Client(base_url=base_url, follow_redirects=True, timeout=60.0)

    # Step 1: Check availability
    if not check_availability(client, args.endpoint):
        print("Agent not available!")
        sys.exit(1)

    # Step 2: Get input schema
    schema = get_input_schema(client, args.endpoint)

    # Step 3: Build input data
    custom_inputs = AGENT_PRESETS.get(args.endpoint, {})
    if args.custom_input:
        custom_inputs.update(json.loads(args.custom_input))

    input_data = build_input_data(schema, custom_inputs)

    # Step 4: Start job
    result = start_job(client, args.endpoint, input_data)

    job_id = result.get("job_id")
    if not job_id:
        print("ERROR: No job_id returned")
        sys.exit(1)

    # Step 5: Execute purchase if requested
    if args.purchase and result.get("status") == "awaiting_payment":
        execute_purchase(result)

    # Step 6: Poll for completion if requested
    if args.poll:
        poll_job_status(client, args.endpoint, job_id, args.poll_interval)

    print(f"\n=== Done ===")
    print(f"Job ID: {job_id}")
    print(f"Status: {result.get('status')}")


if __name__ == "__main__":
    main()
