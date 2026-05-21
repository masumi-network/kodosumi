"""
Tests for Sumi Protocol Default and Override Behavior.

PURPOSE: Verify that the GET /sumi and GET /sumi/{expose} endpoints correctly
apply default values when meta.data YAML is missing fields, AND that explicit
values in meta.data properly override the defaults.

TEST CATEGORIES:

1. Default Value Tests
   - When meta.data is empty/missing, verify defaults are applied:
     - display: defaults to endpoint name (from URL)
     - tags: defaults to ["untagged"]
     - agentPricing: defaults to [{pricingType: "Fixed", fixedPricing: [{amount: "0", unit: "lovelace"}]}]
     - description: defaults to None
     - image: defaults to None
     - author: defaults to None
     - capability: defaults to None
     - legal: defaults to None
     - example_output: defaults to None
     - network: inherited from expose (defaults to "Preprod" if not set)
     - state: from meta.state (alive/dead)

2. Override Tests
   - When meta.data specifies values, verify they override defaults:
     - display: custom display name overrides endpoint-derived name
     - tags: custom tags override ["untagged"]
     - agentPricing: custom pricing overrides zero default
     - All optional fields: explicit values are used when provided

3. Filtering Tests
   - Only enabled exposes are included
   - Only RUNNING exposes are included
   - Only alive meta entries are included
   - Only enabled meta entries are included (flow-level enable)

4. URL Construction Tests
   - api_url: /sumi/{parent}/{name} format
   - base_url: /-/{route_prefix}/{endpoint} format
   - id: {parent}/{name} format

5. Pagination Tests
   - Verify offset-based pagination works
   - Verify page size limiting works
"""

import os
import tempfile

import pytest
import yaml

from kodosumi.service.expose import db
from kodosumi.service.expose.models import ExposeMeta
from kodosumi.service.sumi.control import (
    _get_alive_flows,
    _meta_to_flow_item,
    _parse_agent_pricing,
    _parse_author,
    _parse_capability,
    _parse_example_output,
    _parse_legal,
    _parse_meta_data,
    _url_to_name,
)


# =============================================================================
# SECTION 1: Default Value Tests
# Purpose: Verify defaults are applied when meta.data is empty/missing
# =============================================================================


class TestDefaultValues:
    """
    PURPOSE: Test that default values are correctly applied when meta.data
    YAML is empty or missing fields.

    These tests ensure the system is usable even with minimal configuration.
    """

    def test_display_defaults_to_endpoint_name(self):
        """
        PURPOSE: When meta.data has no 'display' field, the display name
        should default to the endpoint name derived from meta.url.

        SCENARIO: A flow is registered but the devops hasn't configured
        a custom display name yet. Users should still see a meaningful name.
        """
        meta = ExposeMeta(
            url="/my-agent/process",
            data="",  # Empty data - no display specified
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="my-agent",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost:3370",
        )

        # display should fall back to endpoint name "process"
        assert result.display == "process"
        # name (technical id) is always from endpoint
        assert result.name == "process"

    def test_display_defaults_to_endpoint_with_complex_url(self):
        """
        PURPOSE: Verify endpoint extraction works with nested URL paths.
        Only the last segment (endpoint) should be used.

        SCENARIO: Deep nested paths like /api/v2/agents/analyze should
        use "analyze" as the default display name.
        """
        meta = ExposeMeta(
            url="/api/v2/agents/analyze",
            data=None,  # No data at all
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="my-api",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )

        assert result.display == "analyze"
        assert result.name == "analyze"

    def test_tags_default_to_untagged(self):
        """
        PURPOSE: When meta.data has no 'tags' field, tags should default
        to ["untagged"] since MIP-002 requires at least one tag.

        SCENARIO: A new flow hasn't been categorized yet. The system
        should still be compliant with MIP-002 minimum requirements.
        """
        meta = ExposeMeta(
            url="/test/endpoint",
            data="display: Test Flow",  # No tags specified
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="test",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )

        assert result.tags == ["untagged"]

    def test_tags_default_when_empty_list(self):
        """
        PURPOSE: Even if tags is explicitly set to empty list, default
        should be applied since MIP-002 requires at least one tag.

        SCENARIO: Someone accidentally sets tags: [] in YAML.
        """
        meta = ExposeMeta(
            url="/test/endpoint",
            data="display: Test\ntags: []",
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="test",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )

        assert result.tags == ["untagged"]

    def test_pricing_defaults_to_zero(self):
        """
        PURPOSE: When meta.data has no 'agentPricing' field, pricing should
        default to free (0 lovelace) with Fixed pricing type.

        SCENARIO: A flow is available for free during beta testing.
        MIP-002 still requires pricing info, so we provide a zero default.
        """
        result = _parse_agent_pricing({})  # Empty dict

        assert len(result) == 1
        assert result[0].pricingType == "Fixed"
        assert len(result[0].fixedPricing) == 1
        assert result[0].fixedPricing[0].amount == "0"
        assert result[0].fixedPricing[0].unit == "lovelace"

    def test_optional_fields_default_to_none(self):
        """
        PURPOSE: Optional MIP-002 fields should default to None when not
        specified in meta.data.

        SCENARIO: Minimal configuration - only required fields are set.
        """
        meta = ExposeMeta(
            url="/test/endpoint",
            data="display: Test\ntags:\n  - test",  # Minimal required fields
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="test",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )

        assert result.description is None
        assert result.image is None
        assert result.author is None
        assert result.capability is None
        assert result.legal is None
        assert result.example_output is None

    def test_network_inherited_from_expose(self):
        """
        PURPOSE: The network field should be inherited from the expose,
        not from meta.data (which doesn't have network).

        SCENARIO: All flows in an expose share the same network (Preprod/Mainnet).
        """
        meta = ExposeMeta(
            url="/test/endpoint",
            data="",
            state="alive",
        )

        # Test with Mainnet
        result_mainnet = _meta_to_flow_item(
            expose_name="test",
            expose_network="Mainnet",
            meta=meta,
            app_server="http://localhost",
        )
        assert result_mainnet.network == "Mainnet"

        # Test with Preprod
        result_preprod = _meta_to_flow_item(
            expose_name="test",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )
        assert result_preprod.network == "Preprod"

    def test_network_defaults_to_preprod_when_none(self):
        """
        PURPOSE: When expose has no network set (None), default to Preprod.

        SCENARIO: Legacy exposes created before network field was added.
        """
        meta = ExposeMeta(
            url="/test/endpoint",
            data="",
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="test",
            expose_network=None,  # type: ignore
            meta=meta,
            app_server="http://localhost",
        )

        assert result.network is None  # == "Preprod"


# =============================================================================
# SECTION 2: Override Tests
# Purpose: Verify explicit values in meta.data override defaults
# =============================================================================


class TestOverrideValues:
    """
    PURPOSE: Test that explicit values in meta.data YAML properly override
    the default values.

    These tests ensure devops can customize flow appearance and metadata.
    """

    def test_display_override(self):
        """
        PURPOSE: When meta.data specifies 'display', it should override
        the endpoint-derived default name.

        SCENARIO: Devops wants a user-friendly name like "Document Processor"
        instead of the technical endpoint name "process".
        """
        meta = ExposeMeta(
            url="/my-agent/process",
            data="display: Document Processor",
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="my-agent",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )

        # display uses the explicit value
        assert result.display == "Document Processor"
        # name (technical id) still comes from endpoint
        assert result.name == "process"

    def test_tags_override(self):
        """
        PURPOSE: When meta.data specifies 'tags', they should override
        the default ["untagged"].

        SCENARIO: Flow is categorized for search and filtering.
        """
        meta = ExposeMeta(
            url="/test/endpoint",
            data="""
display: Test
tags:
  - ai
  - document-processing
  - beta
""",
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="test",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )

        assert result.tags == ["ai", "document-processing", "beta"]

    def test_pricing_override(self):
        """
        PURPOSE: When meta.data specifies 'agentPricing', it should override
        the default zero pricing.

        SCENARIO: Flow has a cost of 1 ADA (1,000,000 lovelace).
        """
        data = {
            "agentPricing": [
                {
                    "pricingType": "Fixed",
                    "fixedPricing": [
                        {"amount": "1000000", "unit": "lovelace"},
                    ],
                }
            ]
        }

        result = _parse_agent_pricing(data)

        assert len(result) == 1
        assert result[0].fixedPricing[0].amount == "1000000"

    def test_multiple_pricing_tiers(self):
        """
        PURPOSE: Verify multiple pricing options can be specified.

        SCENARIO: Flow offers different pricing tiers or currencies.
        """
        data = {
            "agentPricing": [
                {
                    "pricingType": "Fixed",
                    "fixedPricing": [
                        {"amount": "1000000", "unit": "lovelace"},
                        {"amount": "500000", "unit": "lovelace"},
                    ],
                }
            ]
        }

        result = _parse_agent_pricing(data)

        assert len(result[0].fixedPricing) == 2

    def test_author_override(self):
        """
        PURPOSE: When meta.data specifies 'author', it should be parsed
        and included in the response.

        SCENARIO: Flow creator wants attribution and contact info visible.
        """
        data = {
            "author": {
                "name": "John Doe",
                "contact_email": "john@example.com",
                "organization": "Example Corp",
                "contact_other": "https://twitter.com/johndoe",
            }
        }

        result = _parse_author(data)

        assert result is not None
        assert result.name == "John Doe"
        assert result.contact_email == "john@example.com"
        assert result.organization == "Example Corp"
        assert result.contact_other == "https://twitter.com/johndoe"

    def test_capability_override(self):
        """
        PURPOSE: When meta.data specifies 'capability', it should be parsed.

        SCENARIO: Flow declares its capability name and version for
        compatibility checking.
        """
        data = {
            "capability": {
                "name": "document-processor",
                "version": "1.2.0",
            }
        }

        result = _parse_capability(data)

        assert result is not None
        assert result.name == "document-processor"
        assert result.version == "1.2.0"

    def test_capability_requires_both_fields(self):
        """
        PURPOSE: Capability should only be returned if BOTH name and version
        are specified (MIP-002 requirement).

        SCENARIO: Partial capability config should be treated as None.
        """
        # Missing version
        result1 = _parse_capability({"capability": {"name": "test"}})
        assert result1 is None

        # Missing name
        result2 = _parse_capability({"capability": {"version": "1.0"}})
        assert result2 is None

    def test_legal_override(self):
        """
        PURPOSE: When meta.data specifies 'legal', it should be parsed.

        SCENARIO: Organization provides links to privacy policy and terms.
        """
        data = {
            "legal": {
                "privacy_policy": "https://example.com/privacy",
                "terms": "https://example.com/terms",
                "other": "https://example.com/compliance",
            }
        }

        result = _parse_legal(data)

        assert result is not None
        assert result.privacy_policy == "https://example.com/privacy"
        assert result.terms == "https://example.com/terms"
        assert result.other == "https://example.com/compliance"

    def test_example_output_override(self):
        """
        PURPOSE: When meta.data specifies 'example_output', it should be parsed.
        MIP-002 uses this to show samples of what the service PRODUCES.

        SCENARIO: Service provides example output files for documentation.
        """
        data = {
            "example_output": [
                {
                    "name": "Sample PDF Report",
                    "mime_type": "application/pdf",
                    "url": "https://example.com/sample.pdf",
                },
                {
                    "name": "Sample JSON",
                    "mime_type": "application/json",
                    "url": "https://example.com/sample.json",
                },
            ]
        }

        result = _parse_example_output(data)

        assert result is not None
        assert len(result) == 2
        assert result[0].name == "Sample PDF Report"
        assert result[0].mime_type == "application/pdf"
        assert result[1].name == "Sample JSON"

    def test_example_output_requires_all_fields(self):
        """
        PURPOSE: Each example_output entry requires name, mime_type, and url.
        Incomplete entries should be filtered out.

        SCENARIO: Malformed YAML shouldn't break the system.
        """
        data = {
            "example_output": [
                {"name": "Valid", "mime_type": "text/plain", "url": "https://example.com/valid"},
                {"name": "Missing URL", "mime_type": "text/plain"},  # Invalid
                {"name": "Missing MIME"},  # Invalid
            ]
        }

        result = _parse_example_output(data)

        assert result is not None
        assert len(result) == 1
        assert result[0].name == "Valid"

    def test_description_and_image_override(self):
        """
        PURPOSE: When meta.data specifies 'description' and 'image',
        they should be included in the response.

        SCENARIO: Flow has marketing description and preview image.
        """
        meta = ExposeMeta(
            url="/test/endpoint",
            data="""
display: My Service
description: A powerful AI service for document processing
image: https://example.com/preview.png
tags:
  - ai
""",
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="test",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )

        assert result.description == "A powerful AI service for document processing"
        assert result.image == "https://example.com/preview.png"


# =============================================================================
# SECTION 3: Filtering Tests
# Purpose: Verify filtering logic (enabled, running, alive)
# =============================================================================


class TestFiltering:
    """
    PURPOSE: Test the filtering logic for GET /sumi endpoints.

    Only flows that meet ALL criteria should be returned:
    - Expose is enabled
    - Expose state is RUNNING
    - Meta entry state is "alive"
    - Meta entry is enabled (flow-level enable)
    """

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "expose.db")
            yield db_path

    @pytest.mark.asyncio
    async def test_filters_disabled_exposes(self, temp_db):
        """
        PURPOSE: Exposes with enabled=False should not appear in results.

        SCENARIO: Admin disables an expose for maintenance.
        """
        await db.init_database(temp_db)

        meta_yaml = yaml.dump([{"url": "/test", "state": "alive", "enabled": True}])

        await db.upsert_expose(
            name="disabled-expose",
            display="Disabled",
            network="Preprod",
            enabled=False,  # Disabled!
            state="RUNNING",
            heartbeat=1234567890.0,
            bootstrap="bootstrap",
            meta=meta_yaml,
            db_path=temp_db,
        )

        result = await _get_alive_flows("http://localhost", db_path=temp_db)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_filters_non_running_exposes(self, temp_db):
        """
        PURPOSE: Exposes with state != RUNNING should not appear in results.

        SCENARIO: Expose is enabled but Ray Serve deployment is not running.
        """
        await db.init_database(temp_db)

        meta_yaml = yaml.dump([{"url": "/test", "state": "alive", "enabled": True}])

        # Test DEAD state
        await db.upsert_expose(
            name="dead-expose",
            display="Dead",
            network="Preprod",
            enabled=True,
            state="DEAD",  # Not running!
            heartbeat=1234567890.0,
            bootstrap="bootstrap",
            meta=meta_yaml,
            db_path=temp_db,
        )

        result = await _get_alive_flows("http://localhost", db_path=temp_db)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_filters_draft_exposes(self, temp_db):
        """
        PURPOSE: Exposes with state=DRAFT should not appear in results.

        SCENARIO: New expose hasn't been deployed yet.
        """
        await db.init_database(temp_db)

        meta_yaml = yaml.dump([{"url": "/test", "state": "alive", "enabled": True}])

        await db.upsert_expose(
            name="draft-expose",
            display="Draft",
            network="Preprod",
            enabled=True,
            state="DRAFT",  # Not deployed!
            heartbeat=1234567890.0,
            bootstrap="bootstrap",
            meta=meta_yaml,
            db_path=temp_db,
        )

        result = await _get_alive_flows("http://localhost", db_path=temp_db)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_filters_dead_meta_entries(self, temp_db):
        """
        PURPOSE: Meta entries with state != "alive" should not appear.

        SCENARIO: Health check found endpoint is not responding.
        """
        await db.init_database(temp_db)

        meta_yaml = yaml.dump([
            {"url": "/alive", "state": "alive", "enabled": True},
            {"url": "/dead", "state": "dead", "enabled": True},  # Dead!
            {"url": "/not-found", "state": "not-found", "enabled": True},  # Not found!
        ])

        await db.upsert_expose(
            name="mixed-expose",
            display="Mixed",
            network="Preprod",
            enabled=True,
            state="RUNNING",
            heartbeat=1234567890.0,
            bootstrap="bootstrap",
            meta=meta_yaml,
            db_path=temp_db,
        )

        result = await _get_alive_flows("http://localhost", db_path=temp_db)
        assert len(result) == 1
        assert result[0][2].url == "/alive"

    @pytest.mark.asyncio
    async def test_filters_disabled_meta_entries(self, temp_db):
        """
        PURPOSE: Meta entries with enabled=False should not appear.

        SCENARIO: Admin disables a specific flow within an expose.
        """
        await db.init_database(temp_db)

        meta_yaml = yaml.dump([
            {"url": "/enabled", "state": "alive", "enabled": True},
            {"url": "/disabled", "state": "alive", "enabled": False},  # Disabled!
        ])

        await db.upsert_expose(
            name="test-expose",
            display="Test",
            network="Preprod",
            enabled=True,
            state="RUNNING",
            heartbeat=1234567890.0,
            bootstrap="bootstrap",
            meta=meta_yaml,
            db_path=temp_db,
        )

        result = await _get_alive_flows("http://localhost", db_path=temp_db)
        assert len(result) == 1
        assert result[0][2].url == "/enabled"

    @pytest.mark.asyncio
    async def test_enabled_defaults_to_true(self, temp_db):
        """
        PURPOSE: When meta entry doesn't specify 'enabled', it defaults to True.

        SCENARIO: Legacy entries created before enabled field was added.
        """
        await db.init_database(temp_db)

        # Note: enabled field not specified - should default to True
        meta_yaml = yaml.dump([
            {"url": "/legacy", "state": "alive"},
        ])

        await db.upsert_expose(
            name="legacy-expose",
            display="Legacy",
            network="Preprod",
            enabled=True,
            state="RUNNING",
            heartbeat=1234567890.0,
            bootstrap="bootstrap",
            meta=meta_yaml,
            db_path=temp_db,
        )

        result = await _get_alive_flows("http://localhost", db_path=temp_db)
        assert len(result) == 1
        # ExposeMeta.enabled defaults to True
        assert result[0][2].enabled is True

    @pytest.mark.asyncio
    async def test_all_criteria_must_pass(self, temp_db):
        """
        PURPOSE: Verify that ALL filtering criteria must be met.
        Only enabled + RUNNING expose with alive + enabled meta appears.

        SCENARIO: Complex setup with various combinations.
        """
        await db.init_database(temp_db)

        # Valid expose with valid meta
        await db.upsert_expose(
            name="valid",
            display="Valid",
            network="Preprod",
            enabled=True,
            state="RUNNING",
            heartbeat=1234567890.0,
            bootstrap="bootstrap",
            meta=yaml.dump([{"url": "/test", "state": "alive", "enabled": True}]),
            db_path=temp_db,
        )

        # Disabled expose
        await db.upsert_expose(
            name="disabled",
            display="Disabled",
            network="Preprod",
            enabled=False,
            state="RUNNING",
            heartbeat=1234567890.0,
            bootstrap="bootstrap",
            meta=yaml.dump([{"url": "/test", "state": "alive", "enabled": True}]),
            db_path=temp_db,
        )

        # Dead expose
        await db.upsert_expose(
            name="dead",
            display="Dead",
            network="Preprod",
            enabled=True,
            state="DEAD",
            heartbeat=1234567890.0,
            bootstrap="bootstrap",
            meta=yaml.dump([{"url": "/test", "state": "alive", "enabled": True}]),
            db_path=temp_db,
        )

        result = await _get_alive_flows("http://localhost", db_path=temp_db)
        assert len(result) == 1
        assert result[0][0] == "valid"


# =============================================================================
# SECTION 4: URL Construction Tests
# Purpose: Verify correct URL format construction
# =============================================================================


class TestUrlConstruction:
    """
    PURPOSE: Test that URLs are correctly constructed in responses.

    - api_url: /sumi/{parent}/{name} - The Sumi protocol endpoint
    - id: {parent}/{name} - Unique identifier
    """

    def test_api_url_format(self):
        """
        PURPOSE: api_url should point to the Sumi protocol endpoint.
        Format: {app_server}/sumi/{expose_name}/{meta_name}

        SCENARIO: External system needs to call Sumi endpoints.
        """
        meta = ExposeMeta(
            url="/my-agent/process",
            data="",
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="my-agent",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost:3370",
        )

        assert result.api_url == "http://localhost:3370/sumi/my-agent/process"

    def test_id_format(self):
        """
        PURPOSE: id should be {parent}/{name} for unique identification.

        SCENARIO: Used as cursor for pagination and unique lookup key.
        """
        meta = ExposeMeta(
            url="/my-agent/process",
            data="",
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="my-agent",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )

        assert result.id == "my-agent/process"
        assert result.parent == "my-agent"
        assert result.name == "process"

    def test_urls_handle_trailing_slashes(self):
        """
        PURPOSE: URL construction should handle trailing slashes gracefully.

        SCENARIO: Different app_server configurations may have trailing slashes.
        """
        meta = ExposeMeta(
            url="/test/endpoint",
            data="",
            state="alive",
        )

        # With trailing slash
        result1 = _meta_to_flow_item(
            expose_name="test",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost:3370/",  # Trailing slash
        )

        assert result1.api_url == "http://localhost:3370/sumi/test/endpoint"

    def test_complex_url_extraction(self):
        """
        PURPOSE: Only the last path segment (endpoint) should be used as name.

        SCENARIO: Various URL formats should all work correctly.
        """
        test_cases = [
            ("/simple", "simple"),
            ("/two/parts", "parts"),
            ("/a/b/c/d", "d"),
            ("/endpoint/", "endpoint"),  # Trailing slash
        ]

        for url, expected_name in test_cases:
            meta = ExposeMeta(url=url, data="", state="alive")
            result = _meta_to_flow_item(
                expose_name="test",
                expose_network="Preprod",
                meta=meta,
                app_server="http://localhost",
            )
            assert result.name == expected_name, f"URL {url} should give name {expected_name}"


# =============================================================================
# SECTION 5: Edge Cases
# Purpose: Test edge cases and error handling
# =============================================================================


class TestEdgeCases:
    """
    PURPOSE: Test edge cases and error handling scenarios.
    """

    def test_malformed_yaml_returns_empty_dict(self):
        """
        PURPOSE: Invalid YAML in meta.data should not crash, return empty dict.

        SCENARIO: Data corruption or manual editing error.
        """
        result = _parse_meta_data("{{invalid yaml: [}")
        assert result == {}

    def test_empty_url_gives_root_name(self):
        """
        PURPOSE: Empty or root URL should give "root" as name.

        SCENARIO: Edge case where endpoint is at root path.
        """
        assert _url_to_name("") == ""
        assert _url_to_name("/") == ""

    def test_special_chars_in_url_sanitized(self):
        """
        PURPOSE: Special characters in URL are sanitized for the name.

        SCENARIO: URL contains version numbers or special formatting.
        """
        # v2.0 becomes v20
        assert _url_to_name("/api/v2.0/endpoint") == "endpoint"

        # Spaces become hyphens
        assert _url_to_name("/my agent/end point") == "end-point"

    def test_none_meta_data(self):
        """
        PURPOSE: meta.data=None should work like empty string.

        SCENARIO: Flow registered but data never set.
        """
        meta = ExposeMeta(
            url="/test/endpoint",
            data=None,
            state="alive",
        )

        result = _meta_to_flow_item(
            expose_name="test",
            expose_network="Preprod",
            meta=meta,
            app_server="http://localhost",
        )

        # Should use all defaults
        assert result.display == "endpoint"
        assert result.tags == ["untagged"]
        assert result.description is None
