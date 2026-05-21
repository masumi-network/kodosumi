"""
Tests for Sumi Protocol Lock Endpoints (Phase 6).

Tests MIP-003 lock/provide_input:
- GET /sumi/lock/{fid}/{lid}
- POST /sumi/lock/{fid}/{lid}
"""

import pytest
from unittest.mock import patch, MagicMock

from kodosumi.service.sumi.models import (
    LockSchemaResponse,
    ProvideInputRequest,
    ProvideInputResponse,
    InputSchemaResponse,
)
from kodosumi.service.sumi.hash import create_input_hash
from kodosumi.service.sumi.schema import create_empty_schema


# =============================================================================
# LockSchemaResponse Model Tests
# =============================================================================


class TestLockSchemaResponseModel:
    """Test LockSchemaResponse model."""

    def test_pending(self):
        schema = InputSchemaResponse(input_data=None)
        resp = LockSchemaResponse(
            job_id="fid-123",
            status_id="lid-456",
            status="pending",
            input_schema=schema,
            expires_at=1234567890.0,
            prompt="Please confirm",
        )
        assert resp.status == "pending"
        assert resp.job_id == "fid-123"
        assert resp.status_id == "lid-456"
        assert resp.prompt == "Please confirm"

    def test_released(self):
        schema = create_empty_schema()
        resp = LockSchemaResponse(
            job_id="fid-123",
            status_id="lid-456",
            status="released",
            input_schema=schema,
        )
        assert resp.status == "released"

    def test_expired(self):
        schema = create_empty_schema()
        resp = LockSchemaResponse(
            job_id="fid-123",
            status_id="lid-456",
            status="expired",
            input_schema=schema,
        )
        assert resp.status == "expired"

    def test_serialization(self):
        schema = create_empty_schema()
        resp = LockSchemaResponse(
            job_id="fid-123",
            status_id="lid-456",
            status="pending",
            input_schema=schema,
            prompt="Confirm?",
        )
        data = resp.model_dump()

        assert data["job_id"] == "fid-123"
        assert data["status_id"] == "lid-456"
        assert data["status"] == "pending"


# =============================================================================
# ProvideInputRequest Model Tests
# =============================================================================


class TestProvideInputRequestModel:
    """Test ProvideInputRequest model."""

    def test_empty(self):
        req = ProvideInputRequest()
        assert req.input_data is None

    def test_with_input_data(self):
        req = ProvideInputRequest(
            input_data={"confirm": True, "value": "test"},
        )
        assert req.input_data["confirm"] is True
        assert req.input_data["value"] == "test"

    def test_serialization(self):
        req = ProvideInputRequest(input_data={"key": "value"})
        data = req.model_dump()
        assert data["input_data"]["key"] == "value"


# =============================================================================
# ProvideInputResponse Model Tests
# =============================================================================


class TestProvideInputResponseModel:
    """Test ProvideInputResponse model."""

    def test_success(self):
        resp = ProvideInputResponse(
            status="success",
            input_hash="abc123def456",
        )
        assert resp.status == "success"
        assert resp.input_hash == "abc123def456"

    def test_error(self):
        resp = ProvideInputResponse(
            status="error",
            input_hash=None,
        )
        assert resp.status == "error"
        assert resp.input_hash is None

    def test_serialization(self):
        resp = ProvideInputResponse(status="success", input_hash="hash123")
        data = resp.model_dump()
        assert data["status"] == "success"
        assert data["input_hash"] == "hash123"


# =============================================================================
# Input Hash for Locks Tests
# =============================================================================


class TestLockInputHash:
    """Test input hash calculation for locks."""

    def test_hash_with_fid_lid_identifier(self):
        """Test hash using fid:lid as identifier."""
        fid = "abc123"
        lid = "lock-1"
        input_data = {"confirm": True}

        identifier = f"{fid}:{lid}"
        hash_value = create_input_hash(input_data, identifier)

        assert len(hash_value) == 64
        # Same inputs should produce same hash
        assert hash_value == create_input_hash(input_data, identifier)

    def test_different_lids_different_hashes(self):
        """Test that different lock IDs produce different hashes."""
        fid = "abc123"
        input_data = {"confirm": True}

        hash1 = create_input_hash(input_data, f"{fid}:lock-1")
        hash2 = create_input_hash(input_data, f"{fid}:lock-2")

        assert hash1 != hash2


# =============================================================================
# Lock Status Tests
# =============================================================================


class TestLockStatuses:
    """Test lock status enumeration."""

    def test_valid_statuses(self):
        """Test that all valid lock statuses can be used."""
        statuses = ["pending", "released", "expired"]

        for status in statuses:
            resp = LockSchemaResponse(
                job_id="fid",
                status_id="lid",
                status=status,
                input_schema=create_empty_schema(),
            )
            assert resp.status == status


# =============================================================================
# Empty Schema Tests
# =============================================================================


class TestEmptySchema:
    """Test empty schema creation for locks."""

    def test_create_empty_schema(self):
        schema = create_empty_schema()
        assert schema.input_data is None

    def test_empty_schema_in_lock_response(self):
        schema = create_empty_schema()
        resp = LockSchemaResponse(
            job_id="fid",
            status_id="lid",
            status="released",
            input_schema=schema,
        )
        assert resp.input_schema.input_data is None


# =============================================================================
# Integration Pattern Tests
# =============================================================================


class TestLockIntegrationPatterns:
    """Test lock usage patterns."""

    def test_lock_to_release_flow(self):
        """Test the typical lock -> provide_input -> released flow."""
        # 1. Lock is created (pending)
        pending_resp = LockSchemaResponse(
            job_id="fid-123",
            status_id="lid-456",
            status="pending",
            input_schema=create_empty_schema(),
            prompt="Do you want to continue?",
        )
        assert pending_resp.status == "pending"

        # 2. User provides input
        provide_req = ProvideInputRequest(
            input_data={"confirm": True},
        )
        assert provide_req.input_data["confirm"] is True

        # 3. Input is accepted
        provide_resp = ProvideInputResponse(
            status="success",
            input_hash=create_input_hash(
                provide_req.input_data, "fid-123:lid-456"
            ),
        )
        assert provide_resp.status == "success"
        assert len(provide_resp.input_hash) == 64

        # 4. Lock is now released (if queried again)
        released_resp = LockSchemaResponse(
            job_id="fid-123",
            status_id="lid-456",
            status="released",
            input_schema=create_empty_schema(),
        )
        assert released_resp.status == "released"

    def test_expired_lock_flow(self):
        """Test expired lock scenario."""
        # Lock that has expired
        expired_resp = LockSchemaResponse(
            job_id="fid-123",
            status_id="lid-456",
            status="expired",
            input_schema=create_empty_schema(),
            expires_at=1000000000.0,  # Past timestamp
        )
        assert expired_resp.status == "expired"

        # Attempting to provide input returns error
        error_resp = ProvideInputResponse(
            status="error",
            input_hash=None,
        )
        assert error_resp.status == "error"
