"""
Tests for Sumi Protocol models (Phase 1).

Tests MIP-002/MIP-003 Pydantic models and MIP-004 hash calculation.
"""

import pytest

from kodosumi.service.sumi.models import (
    AuthorInfo,
    CapabilityInfo,
    LegalInfo,
    FixedPricing,
    AgentPricing,
    ExampleOutput,
    SumiFlowItem,
    SumiFlowListResponse,
    SumiServiceDetail,
    AvailabilityResponse,
    InputField,
    InputSchemaResponse,
    StartJobRequest,
    StartJobResponse,
    JobStatusResponse,
    LockInputSchema,
    LockSchemaResponse,
    ProvideInputRequest,
    ProvideInputResponse,
    ErrorResponse,
)
from kodosumi.service.sumi.hash import create_input_hash, verify_input_hash


# =============================================================================
# MIP-002 Model Tests
# =============================================================================


class TestAuthorInfo:
    def test_empty(self):
        author = AuthorInfo()
        assert author.name is None
        assert author.organization is None

    def test_full(self):
        author = AuthorInfo(
            name="John Doe",
            contact_email="john@example.com",
            contact_other="@johndoe",
            organization="Example Corp",
        )
        assert author.name == "John Doe"
        assert author.contact_email == "john@example.com"
        assert author.organization == "Example Corp"

    def test_serialization(self):
        author = AuthorInfo(name="Test", organization="Org")
        data = author.model_dump()
        assert data["name"] == "Test"
        assert data["organization"] == "Org"

        # Round-trip
        author2 = AuthorInfo.model_validate(data)
        assert author2.name == author.name


class TestCapabilityInfo:
    def test_required_fields(self):
        cap = CapabilityInfo(name="my-capability", version="1.0.0")
        assert cap.name == "my-capability"
        assert cap.version == "1.0.0"

    def test_missing_field_raises(self):
        with pytest.raises(Exception):
            CapabilityInfo(name="test")  # Missing version


class TestAgentPricing:
    def test_fixed_pricing(self):
        pricing = AgentPricing(
            pricingType="Fixed",
            fixedPricing=[
                FixedPricing(amount="1000000", unit="lovelace"),
                FixedPricing(amount="2000000", unit="lovelace"),
            ],
        )
        assert pricing.pricingType == "Fixed"
        assert len(pricing.fixedPricing) == 2
        assert pricing.fixedPricing[0].amount == "1000000"
        assert pricing.fixedPricing[0].unit == "lovelace"

    def test_serialization(self):
        pricing = AgentPricing(
            fixedPricing=[FixedPricing(amount="1000000", unit="lovelace")]
        )
        data = pricing.model_dump()
        assert data["pricingType"] == "Fixed"

        # Round-trip
        pricing2 = AgentPricing.model_validate(data)
        assert pricing2.fixedPricing[0].amount == "1000000"


class TestExampleOutput:
    def test_create(self):
        example = ExampleOutput(
            name="Sample Report",
            mime_type="application/pdf",
            url="https://example.com/sample.pdf",
        )
        assert example.name == "Sample Report"
        assert example.mime_type == "application/pdf"
        assert example.url == "https://example.com/sample.pdf"


# =============================================================================
# Discovery Model Tests
# =============================================================================


class TestSumiFlowItem:
    def test_minimal(self):
        # id is {parent}/{name}, name is the technical identifier
        item = SumiFlowItem(
            id="test-expose/test",
            parent="test-expose",
            name="test",
            display="Test Flow",
            api_url="https://api.example.com/sumi/test-expose/test",
            tags=["test"],
            agentPricing=[
                AgentPricing(
                    fixedPricing=[FixedPricing(amount="1000000", unit="lovelace")]
                )
            ],
            network="Preprod",
            state="alive",
        )
        assert item.id == "test-expose/test"
        assert item.name == "test"
        assert item.metadata_version == 1  # Default

    def test_with_optional(self):
        item = SumiFlowItem(
            id="test-expose/test",
            parent="test-expose",
            name="test",
            display="Test Flow",
            api_url="https://api.example.com/sumi/test-expose/test",
            tags=["test", "ai"],
            agentPricing=[
                AgentPricing(
                    fixedPricing=[FixedPricing(amount="1000000", unit="lovelace")]
                )
            ],
            network="Mainnet",
            state="alive",
            description="A test flow",
            image="https://example.com/image.png",
            author=AuthorInfo(name="Test Author"),
        )
        assert item.description == "A test flow"
        assert item.author.name == "Test Author"


class TestSumiFlowListResponse:
    def test_empty(self):
        response = SumiFlowListResponse(items=[])
        assert response.items == []
        assert response.offset is None

    def test_with_items(self):
        # id is {parent}/{name}
        item = SumiFlowItem(
            id="test/endpoint",
            parent="test",
            name="endpoint",
            display="Flow",
            api_url="https://example.com/sumi/test/endpoint",
            tags=["test"],
            agentPricing=[
                AgentPricing(
                    fixedPricing=[FixedPricing(amount="1000000", unit="lovelace")]
                )
            ],
            network="Preprod",
            state="alive",
        )
        response = SumiFlowListResponse(items=[item], offset="test/endpoint")
        assert len(response.items) == 1
        assert response.offset == "test/endpoint"


class TestSumiServiceDetail:
    def test_full(self):
        # id is {parent}/{name}
        detail = SumiServiceDetail(
            id="expose/endpoint",
            parent="expose",
            name="endpoint",
            display="Service Name",
            api_url="https://example.com/sumi/expose/endpoint",
            tags=["ai", "document"],
            agentPricing=[
                AgentPricing(
                    fixedPricing=[FixedPricing(amount="1000000", unit="lovelace")]
                )
            ],
            description="A service",
            image="https://example.com/image.png",
            example_output=[
                ExampleOutput(
                    name="Sample",
                    mime_type="application/json",
                    url="https://example.com/sample.json",
                )
            ],
            author=AuthorInfo(name="Author", organization="Org"),
            capability=CapabilityInfo(name="cap", version="1.0"),
            legal=LegalInfo(terms="https://example.com/terms"),
            network="Preprod",
            state="alive",
        )
        assert detail.id == "expose/endpoint"
        assert detail.name == "endpoint"
        assert len(detail.example_output) == 1
        assert detail.capability.name == "cap"


# =============================================================================
# MIP-003 Model Tests
# =============================================================================


class TestAvailabilityResponse:
    def test_available(self):
        resp = AvailabilityResponse(
            status="available", message="Service is ready"
        )
        assert resp.status == "available"
        assert resp.type == "masumi-agent"  # Default
        assert resp.message == "Service is ready"

    def test_unavailable(self):
        resp = AvailabilityResponse(status="unavailable")
        assert resp.status == "unavailable"


class TestInputSchemaResponse:
    def test_with_input_data(self):
        field = InputField(
            id="query",
            type="text",  # Valid type from Literal
            name="Search Query",
            data={"description": "Enter query"},
            validations=[{"validation": "min", "value": "1"}],
        )
        resp = InputSchemaResponse(input_data=[field])
        assert len(resp.input_data) == 1
        assert resp.input_data[0].id == "query"


class TestStartJobRequest:
    def test_minimal(self):
        req = StartJobRequest(identifier_from_purchaser="order-123")
        assert req.identifier_from_purchaser == "order-123"
        assert req.input_data is None

    def test_with_input(self):
        req = StartJobRequest(
            identifier_from_purchaser="order-456",
            input_data={"query": "test", "count": 10},
        )
        assert req.input_data["query"] == "test"


class TestStartJobResponse:
    def test_success(self):
        resp = StartJobResponse(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="success",
            identifierFromPurchaser="order-123",
            input_hash="abc123",
            status_url="/sumi/status/550e8400-e29b-41d4-a716-446655440000",
        )
        assert resp.status == "success"
        assert resp.input_hash == "abc123"


class TestJobStatusResponse:
    def test_running(self):
        resp = JobStatusResponse(job_id="123", status="running")
        assert resp.status == "running"
        assert resp.result is None

    def test_completed(self):
        resp = JobStatusResponse(
            job_id="123",
            status="completed",
            result={"output": "done"},
        )
        assert resp.status == "completed"
        assert resp.result["output"] == "done"

    def test_awaiting_input(self):
        # JobStatusResponse.input_schema expects List[LockInputSchema]
        lock_schema = LockInputSchema(
            lock_id="lock-1",
            input_data=[InputField(id="confirm", type="boolean")],
        )
        resp = JobStatusResponse(
            job_id="123",
            status="awaiting_input",
            input_schema=[lock_schema],
        )
        assert resp.status == "awaiting_input"
        assert resp.input_schema is not None
        assert len(resp.input_schema) == 1
        assert resp.input_schema[0].lock_id == "lock-1"


class TestLockSchemaResponse:
    def test_pending(self):
        schema = InputSchemaResponse(
            input_data=[InputField(id="answer", type="text")]  # Valid type from Literal
        )
        resp = LockSchemaResponse(
            job_id="fid-123",
            status_id="lid-456",
            status="pending",
            input_schema=schema,
            prompt="Please confirm",
        )
        assert resp.status == "pending"
        assert resp.prompt == "Please confirm"


class TestProvideInputResponse:
    def test_success(self):
        resp = ProvideInputResponse(status="success", input_hash="def456")
        assert resp.status == "success"
        assert resp.input_hash == "def456"


class TestErrorResponse:
    def test_basic(self):
        resp = ErrorResponse(message="Not found")
        assert resp.status == "error"
        assert resp.message == "Not found"
        assert resp.code is None

    def test_with_code(self):
        resp = ErrorResponse(message="Invalid input", code="INVALID_INPUT")
        assert resp.code == "INVALID_INPUT"


# =============================================================================
# MIP-004 Hash Tests
# =============================================================================


class TestCreateInputHash:
    def test_basic(self):
        """Test basic hash creation."""
        hash_value = create_input_hash(
            {"query": "hello"}, "order-123"
        )
        assert isinstance(hash_value, str)
        assert len(hash_value) == 64  # SHA-256 hex is 64 chars

    def test_empty_input(self):
        """Test with empty/None input_data."""
        hash1 = create_input_hash(None, "order-123")
        hash2 = create_input_hash({}, "order-123")
        assert hash1 == hash2  # Both should produce same hash

    def test_deterministic(self):
        """Test same inputs produce same hash."""
        input_data = {"a": 1, "b": "test"}
        hash1 = create_input_hash(input_data, "id-1")
        hash2 = create_input_hash(input_data, "id-1")
        assert hash1 == hash2

    def test_different_identifier_different_hash(self):
        """Test different identifiers produce different hashes."""
        input_data = {"query": "test"}
        hash1 = create_input_hash(input_data, "order-1")
        hash2 = create_input_hash(input_data, "order-2")
        assert hash1 != hash2

    def test_different_input_different_hash(self):
        """Test different inputs produce different hashes."""
        identifier = "order-123"
        hash1 = create_input_hash({"a": 1}, identifier)
        hash2 = create_input_hash({"a": 2}, identifier)
        assert hash1 != hash2

    def test_key_order_invariant(self):
        """Test that key order doesn't affect hash (canonical JSON)."""
        identifier = "order-123"
        hash1 = create_input_hash({"b": 2, "a": 1}, identifier)
        hash2 = create_input_hash({"a": 1, "b": 2}, identifier)
        assert hash1 == hash2

    def test_nested_objects(self):
        """Test with nested objects."""
        input_data = {
            "outer": {
                "inner": {"value": 42},
                "list": [1, 2, 3],
            }
        }
        hash_value = create_input_hash(input_data, "nested-test")
        assert len(hash_value) == 64


class TestVerifyInputHash:
    def test_valid_hash(self):
        """Test verification with correct hash."""
        input_data = {"test": "value"}
        identifier = "order-123"
        hash_value = create_input_hash(input_data, identifier)

        assert verify_input_hash(input_data, identifier, hash_value) is True

    def test_invalid_hash(self):
        """Test verification with incorrect hash."""
        input_data = {"test": "value"}
        identifier = "order-123"

        assert verify_input_hash(input_data, identifier, "wrong-hash") is False

    def test_case_insensitive(self):
        """Test hash comparison is case-insensitive."""
        input_data = {"test": "value"}
        identifier = "order-123"
        hash_value = create_input_hash(input_data, identifier)

        assert verify_input_hash(input_data, identifier, hash_value.upper()) is True
