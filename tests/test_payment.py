"""
Tests for the Masumi payment integration module.
"""

import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from kodosumi.runner.payment import (
    MasumiClient,
    PaymentError,
    PaymentTimeoutError,
    PaymentInitError,
    PaymentSubmitError,
    create_result_hash,
)
from kodosumi.config import MasumiConfig, Settings, _parse_masumi_env, _MASUMI_RE


def _make_config(**overrides) -> MasumiConfig:
    """Helper to create a MasumiConfig with sensible defaults."""
    defaults = dict(
        network="Preprod",
        base_url="https://test.masumi.network/api/v1",
        token="test-token",
        poll_interval=1.0,
    )
    defaults.update(overrides)
    return MasumiConfig(**defaults)


class TestCreateResultHash:
    """Tests for create_result_hash function."""

    def test_none_result(self):
        """Hash of None should be consistent."""
        result = create_result_hash(None)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex

    def test_string_result(self):
        """Hash of string should be consistent."""
        result1 = create_result_hash("test")
        result2 = create_result_hash("test")
        assert result1 == result2
        assert len(result1) == 64

    def test_dict_result(self):
        """Hash of dict should be deterministic (sorted keys)."""
        result1 = create_result_hash({"b": 2, "a": 1})
        result2 = create_result_hash({"a": 1, "b": 2})
        assert result1 == result2

    def test_different_results_different_hashes(self):
        """Different results should produce different hashes."""
        hash1 = create_result_hash("result1")
        hash2 = create_result_hash("result2")
        assert hash1 != hash2


class TestMasumiClientConfig:
    """Tests for MasumiClient configuration."""

    def test_client_uses_config(self):
        """Client should use values from MasumiConfig."""
        cfg = _make_config(
            base_url="https://test.masumi.network/api/v1",
            token="test-token",
            poll_interval=3.0,
        )
        client = MasumiClient(cfg)

        assert client.base_url == "https://test.masumi.network/api/v1"
        assert client.token == "test-token"
        assert client.poll_interval == 3.0

    def test_base_url_trailing_slash_stripped(self):
        """Base URL trailing slash should be stripped."""
        cfg = _make_config(base_url="https://test.masumi.network/api/v1/")
        client = MasumiClient(cfg)
        assert client.base_url == "https://test.masumi.network/api/v1"


class TestMasumiClientHeaders:
    """Tests for MasumiClient header generation."""

    def test_headers_with_token(self):
        """Headers should include token when configured."""
        cfg = _make_config(token="my-secret-token")
        client = MasumiClient(cfg)
        headers = client._get_headers()

        assert headers["token"] == "my-secret-token"
        assert headers["accept"] == "application/json"
        assert headers["Content-Type"] == "application/json"

    def test_headers_without_token(self):
        """Headers should have empty token when not configured."""
        cfg = _make_config(token="")
        client = MasumiClient(cfg)
        headers = client._get_headers()
        assert headers["token"] == ""


class TestMasumiClientDeadlines:
    """Tests for deadline calculation."""

    def test_deadlines_are_iso_format(self):
        """Deadlines should be in ISO format."""
        cfg = _make_config()
        client = MasumiClient(cfg)
        pay_by, submit_by = client._calculate_deadlines()

        # Should be parseable as ISO format
        datetime.fromisoformat(pay_by.replace("Z", "+00:00"))
        datetime.fromisoformat(submit_by.replace("Z", "+00:00"))

    def test_submit_deadline_after_pay_deadline(self):
        """Submit deadline (3600s) should be after pay deadline (1200s)."""
        cfg = _make_config()
        client = MasumiClient(cfg)
        pay_by, submit_by = client._calculate_deadlines()

        pay_dt = datetime.fromisoformat(pay_by.replace("Z", "+00:00"))
        submit_dt = datetime.fromisoformat(submit_by.replace("Z", "+00:00"))

        assert submit_dt > pay_dt


class TestMasumiClientInitPayment:
    """Tests for init_payment method."""

    @pytest.mark.asyncio
    async def test_init_payment_success(self):
        """Successful payment init should return response."""
        cfg = _make_config()
        client = MasumiClient(cfg)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "blockchainIdentifier": "abc123",
                "payByTime": "2025-01-01T00:00:00.000Z"
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await client.init_payment(
                agent_identifier="agent123",
                network="Preprod",
                input_hash="hash123",
                identifier_from_purchaser="purchaser123",
            )

            assert result["data"]["blockchainIdentifier"] == "abc123"

    @pytest.mark.asyncio
    async def test_init_payment_http_error(self):
        """HTTP error should raise PaymentInitError."""
        import httpx

        cfg = _make_config()
        client = MasumiClient(cfg)

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client.post.return_value.raise_for_status.side_effect = (
                httpx.HTTPStatusError(
                    "Error", request=MagicMock(), response=mock_response
                )
            )
            mock_client_class.return_value = mock_client

            with pytest.raises(PaymentInitError) as exc_info:
                await client.init_payment(
                    agent_identifier="agent123",
                    network="Preprod",
                    input_hash="hash123",
                    identifier_from_purchaser="purchaser123",
                )

            assert "400" in str(exc_info.value)


class TestMasumiClientGetPaymentStatus:
    """Tests for get_payment_status method."""

    @pytest.mark.asyncio
    async def test_payment_found(self):
        """Should return payment when found."""
        cfg = _make_config()
        client = MasumiClient(cfg)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "Payments": [
                    {"blockchainIdentifier": "abc123", "onChainState": "FundsLocked"},
                    {"blockchainIdentifier": "other", "onChainState": "Pending"},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await client.get_payment_status("abc123", "Preprod")

            assert result is not None
            assert result["onChainState"] == "FundsLocked"

    @pytest.mark.asyncio
    async def test_payment_not_found(self):
        """Should return None when payment not found."""
        cfg = _make_config()
        client = MasumiClient(cfg)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "Payments": [
                    {"blockchainIdentifier": "other", "onChainState": "Pending"},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await client.get_payment_status("abc123", "Preprod")

            assert result is None


class TestMasumiClientSubmitResult:
    """Tests for submit_result method."""

    @pytest.mark.asyncio
    async def test_submit_success(self):
        """Successful submit should return response."""
        cfg = _make_config()
        client = MasumiClient(cfg)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success"}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await client.submit_result(
                blockchain_identifier="abc123",
                network="Preprod",
                result_hash="hash123",
            )

            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_submit_http_error(self):
        """HTTP error should raise PaymentSubmitError."""
        import httpx

        cfg = _make_config()
        client = MasumiClient(cfg)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server error"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client.post.return_value.raise_for_status.side_effect = (
                httpx.HTTPStatusError(
                    "Error", request=MagicMock(), response=mock_response
                )
            )
            mock_client_class.return_value = mock_client

            with pytest.raises(PaymentSubmitError) as exc_info:
                await client.submit_result(
                    blockchain_identifier="abc123",
                    network="Preprod",
                    result_hash="hash123",
                )

            assert "500" in str(exc_info.value)


class TestMasumiConfigParsing:
    """Tests for MASUMI env var parsing."""

    @staticmethod
    def _clean_masumi_env():
        """Return env dict with all MASUMI-related vars removed."""
        return {k: v for k, v in os.environ.items()
                if not _MASUMI_RE.match(k)}

    def test_parse_single_network(self):
        """Single MASUMI env var should parse correctly."""
        clean = self._clean_masumi_env()
        clean["MASUMI"] = "Preprod https://preprod.api.com tok123 600 1800 2.5"
        with patch.dict(os.environ, clean, clear=True):
            networks = _parse_masumi_env()

        assert "Preprod" in networks
        cfg = networks["Preprod"]
        assert cfg.base_url == "https://preprod.api.com"
        assert cfg.token == "tok123"
        assert cfg.pay_by_time == 600.0
        assert cfg.submit_result_by_time == 1800.0
        assert cfg.poll_interval == 2.5

    def test_parse_multiple_networks(self):
        """Multiple MASUMI env vars should parse correctly."""
        clean = self._clean_masumi_env()
        clean["MASUMI0"] = "Preprod https://preprod.api.com tok_pre 900 1800 1"
        clean["MASUMI1"] = "Mainnet https://mainnet.api.com tok_main 600 3600 2"
        with patch.dict(os.environ, clean, clear=True):
            networks = _parse_masumi_env()

        assert len(networks) >= 2
        assert "Preprod" in networks
        assert "Mainnet" in networks
        assert networks["Mainnet"].token == "tok_main"
        assert networks["Mainnet"].pay_by_time == 600.0
        assert networks["Mainnet"].poll_interval == 2.0

    def test_kodo_prefix_takes_precedence(self):
        """KODO_ prefixed var should override bare var for same suffix."""
        clean = self._clean_masumi_env()
        clean["MASUMI0"] = "Preprod https://bare.api.com bare_tok 1"
        clean["KODO_MASUMI0"] = "Preprod https://prefixed.api.com pre_tok 2"
        with patch.dict(os.environ, clean, clear=True):
            networks = _parse_masumi_env()

        assert networks["Preprod"].base_url == "https://prefixed.api.com"
        assert networks["Preprod"].token == "pre_tok"

    def test_minimal_three_fields_uses_defaults(self):
        """Three fields (network, url, token) should use all defaults."""
        clean = self._clean_masumi_env()
        clean["MASUMI"] = "Preprod https://preprod.api.com tok123"
        with patch.dict(os.environ, clean, clear=True):
            networks = _parse_masumi_env()

        cfg = networks["Preprod"]
        assert cfg.base_url == "https://preprod.api.com"
        assert cfg.token == "tok123"
        assert cfg.pay_by_time == 1200.0
        assert cfg.submit_result_by_time == 3600.0
        assert cfg.poll_interval == 1.0

    def test_optional_fields_partial(self):
        """4 or 5 fields should set the corresponding optional values."""
        clean = self._clean_masumi_env()
        clean["MASUMI"] = "Preprod https://preprod.api.com tok123 600"
        with patch.dict(os.environ, clean, clear=True):
            networks = _parse_masumi_env()
        cfg = networks["Preprod"]
        assert cfg.pay_by_time == 600.0
        assert cfg.submit_result_by_time == 3600.0
        assert cfg.poll_interval == 1.0

        clean["MASUMI"] = "Preprod https://preprod.api.com tok123 600 1800"
        with patch.dict(os.environ, clean, clear=True):
            networks = _parse_masumi_env()
        cfg = networks["Preprod"]
        assert cfg.pay_by_time == 600.0
        assert cfg.submit_result_by_time == 1800.0
        assert cfg.poll_interval == 1.0

    def test_all_six_fields(self):
        """All 6 fields should set all values."""
        clean = self._clean_masumi_env()
        clean["MASUMI"] = "Preprod https://preprod.api.com tok123 600 1800 2.5"
        with patch.dict(os.environ, clean, clear=True):
            networks = _parse_masumi_env()
        cfg = networks["Preprod"]
        assert cfg.pay_by_time == 600.0
        assert cfg.submit_result_by_time == 1800.0
        assert cfg.poll_interval == 2.5

    def test_invalid_format_raises(self):
        """Too few or too many fields should raise ValueError."""
        clean = self._clean_masumi_env()
        clean["MASUMI0"] = "Preprod https://api.com"
        with patch.dict(os.environ, clean, clear=True):
            with pytest.raises(ValueError, match="3-6 space-separated"):
                _parse_masumi_env()

    def test_no_masumi_vars_returns_empty(self):
        """No MASUMI env vars should return empty dict."""
        clean = self._clean_masumi_env()
        with patch.dict(os.environ, clean, clear=True):
            networks = _parse_masumi_env()
        assert networks == {}

    def test_get_masumi_missing_network(self):
        """get_masumi should raise ValueError for unconfigured network."""
        clean = self._clean_masumi_env()
        with patch.dict(os.environ, clean, clear=True):
            s = Settings()
            with pytest.raises(ValueError, match="No Masumi config"):
                s.get_masumi("Mainnet")
