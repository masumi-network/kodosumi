"""Tests for _heal_agent_identifier in sumi/control.py."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def mock_state():
    settings = MagicMock()
    cfg = MagicMock()
    cfg.base_url = "https://example.com/api/v1"
    cfg.token = "test"
    cfg.registry_network = "Preprod"
    settings.get_masumi.return_value = cfg
    return {"settings": settings}


@pytest.mark.asyncio
async def test_heal_returns_none_without_registration_id(mock_state):
    from kodosumi.service.sumi.control import _heal_agent_identifier
    meta = MagicMock()
    with patch("kodosumi.service.expose.db.get_expose", new_callable=AsyncMock, return_value={"network": "Preprod"}):
        result = await _heal_agent_identifier("test_expose", meta, {}, mock_state)
    assert result is None


@pytest.mark.asyncio
async def test_heal_returns_none_without_network(mock_state):
    from kodosumi.service.sumi.control import _heal_agent_identifier
    meta = MagicMock()
    meta_data = {"registrationId": "abc123"}
    with patch("kodosumi.service.expose.db.get_expose", new_callable=AsyncMock, return_value={"network": ""}):
        result = await _heal_agent_identifier("test_expose", meta, meta_data, mock_state)
    assert result is None


@pytest.mark.asyncio
async def test_heal_queries_registry_and_returns_identifier(mock_state):
    from kodosumi.service.sumi.control import _heal_agent_identifier
    meta = MagicMock()
    meta_data = {"registrationId": "abc123", "network": "Preprod"}

    with patch("kodosumi.service.expose.registry.get_registration_status", new_callable=AsyncMock) as mock_reg, \
         patch("kodosumi.service.expose.db.get_expose", new_callable=AsyncMock, return_value=None), \
         patch("kodosumi.service.expose.db.update_expose_meta", new_callable=AsyncMock):
        mock_reg.return_value = {"agentIdentifier": "found_id_123"}
        result = await _heal_agent_identifier("test_expose", meta, meta_data, mock_state)

    assert result == "found_id_123"
    mock_reg.assert_called_once()


@pytest.mark.asyncio
async def test_heal_returns_none_when_registry_has_no_identifier(mock_state):
    from kodosumi.service.sumi.control import _heal_agent_identifier
    meta = MagicMock()
    meta_data = {"registrationId": "abc123", "network": "Preprod"}

    with patch("kodosumi.service.expose.registry.get_registration_status", new_callable=AsyncMock) as mock_reg:
        mock_reg.return_value = {"agentIdentifier": ""}
        result = await _heal_agent_identifier("test_expose", meta, meta_data, mock_state)

    assert result is None


@pytest.mark.asyncio
async def test_heal_returns_none_on_registry_exception(mock_state):
    from kodosumi.service.sumi.control import _heal_agent_identifier
    meta = MagicMock()
    meta_data = {"registrationId": "abc123", "network": "Preprod"}

    with patch("kodosumi.service.expose.registry.get_registration_status", new_callable=AsyncMock) as mock_reg:
        mock_reg.side_effect = Exception("connection refused")
        result = await _heal_agent_identifier("test_expose", meta, meta_data, mock_state)

    assert result is None
