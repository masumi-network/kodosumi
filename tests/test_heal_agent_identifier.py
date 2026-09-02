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
        mock_reg.return_value = {"agentIdentifier": "found_id_123",
                                 "state": "RegistrationConfirmed"}
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


@pytest.mark.asyncio
async def test_heal_persists_through_the_shared_writer(mock_state):
    # Every flow of an expose shares one meta column. The heal runs on the
    # serving path while the admin panel polls, so it has to write through
    # the locked writer instead of rewriting the column itself.
    import yaml

    from kodosumi.service.sumi.control import _heal_agent_identifier

    meta = MagicMock()
    meta.url = "/flow-a"
    meta_data = {"registrationId": "abc123", "network": "Preprod"}
    stored = [
        {"url": "/flow-a",
         "data": yaml.dump({"display": "A", "registrationId": "abc123"},
                           sort_keys=False)},
        {"url": "/flow-b",
         "data": yaml.dump({"display": "B", "registrationId": "def456"},
                           sort_keys=False)},
    ]
    row = {"meta": yaml.dump(stored, sort_keys=False)}

    with patch("kodosumi.service.expose.registry.get_registration_status",
               new_callable=AsyncMock,
               return_value={"agentIdentifier": "found_id_123",
                             "state": "RegistrationConfirmed"}), \
         patch("kodosumi.service.expose.db.get_expose",
               new_callable=AsyncMock, return_value=row), \
         patch("kodosumi.service.expose.flow_meta.db.get_expose",
               new_callable=AsyncMock, return_value=row), \
         patch("kodosumi.service.expose.flow_meta.db.update_expose_meta",
               new_callable=AsyncMock) as mock_write:
        result = await _heal_agent_identifier(
            "test_expose", meta, meta_data, mock_state)

    assert result == "found_id_123"
    saved = yaml.safe_load(mock_write.call_args.args[1])
    by_url = {e["url"]: yaml.safe_load(e["data"]) for e in saved}
    assert by_url["/flow-a"]["agentIdentifier"] == "found_id_123"
    # The sibling flow of the same expose keeps its own registration.
    assert by_url["/flow-b"] == {"display": "B", "registrationId": "def456"}
    # The operator's key order survives, so the editor does not reshuffle.
    assert list(by_url["/flow-a"]) == [
        "display", "registrationId", "agentIdentifier"]
