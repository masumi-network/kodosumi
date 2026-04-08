"""
Tests for the new expose.db-based flow system.

Tests _flows_from_expose(), _get_all_flows(), and the FlowControl endpoints.
"""
import pytest
import yaml
import tempfile
import os

from kodosumi.service.flow import _flows_from_expose, _get_all_flows
from kodosumi.service.expose import db as expose_db


# ─── Test Data ────────────────────────────────────────────────────────

def _make_meta_yaml(entries):
    """Build a meta YAML string from a list of (url, data_dict) tuples."""
    meta_list = []
    for url, data_dict in entries:
        meta_list.append({
            "url": url,
            "data": yaml.dump(data_dict, default_flow_style=False),
            "enabled": True,
            "state": "alive",
            "heartbeat": 1234567890.0,
        })
    return yaml.dump(meta_list, default_flow_style=False)


def _make_row(name, meta_entries=None, enabled=1, network="Preprod"):
    """Build a fake expose DB row dict."""
    meta = _make_meta_yaml(meta_entries) if meta_entries else None
    return {
        "name": name,
        "display": name,
        "network": network,
        "enabled": enabled,
        "state": "RUNNING",
        "heartbeat": 1234567890.0,
        "bootstrap": "",
        "meta": meta,
        "created": 1234567890.0,
        "updated": 1234567890.0,
    }


# ─── Unit Tests: _flows_from_expose() ────────────────────────────────

class TestFlowsFromExpose:

    def test_basic_flow(self):
        row = _make_row("myapp", [
            ("/myapp/analyze", {
                "display": "My Agent",
                "description": "Does things",
                "tags": ["ai", "research"],
                "author": {"name": "Alice", "organization": "Acme"},
            })
        ])
        flows = _flows_from_expose(row)
        assert len(flows) == 1
        f = flows[0]
        assert f.summary == "My Agent"
        assert f.description == "Does things"
        assert f.tags == ["ai", "research"]
        assert f.author == "Alice"
        assert f.organization == "Acme"
        assert f.url == "/-/myapp/myapp/analyze"
        assert f.source == "myapp"
        assert f.method == "POST"
        assert f.deprecated is False

    def test_multiple_flows(self):
        row = _make_row("multi", [
            ("/multi/analyze", {"display": "Flow A", "tags": ["a"]}),
            ("/multi/report", {"display": "Flow B", "tags": ["b"]}),
        ])
        flows = _flows_from_expose(row)
        assert len(flows) == 2
        assert {f.summary for f in flows} == {"Flow A", "Flow B"}

    def test_empty_meta(self):
        row = _make_row("empty", None)
        flows = _flows_from_expose(row)
        assert flows == []

    def test_malformed_yaml(self):
        row = _make_row("bad")
        row["meta"] = "not: [valid: yaml: {{{"
        flows = _flows_from_expose(row)
        assert flows == []

    def test_no_url(self):
        row = _make_row("nurl")
        row["meta"] = yaml.dump([{"data": "display: Test", "enabled": True}])
        flows = _flows_from_expose(row)
        assert flows == []

    def test_disabled_entry(self):
        meta_list = [{
            "url": "/app/run",
            "data": yaml.dump({"display": "Hidden"}),
            "enabled": False,
            "state": "alive",
            "heartbeat": 0.0,
        }]
        row = _make_row("dis")
        row["meta"] = yaml.dump(meta_list)
        flows = _flows_from_expose(row)
        assert flows == []

    def test_fallback_display_to_name(self):
        row = _make_row("fallback", [
            ("/fallback/run", {"description": "No display field"})
        ])
        flows = _flows_from_expose(row)
        assert len(flows) == 1
        assert flows[0].summary == "fallback"

    def test_author_none(self):
        row = _make_row("noauthor", [
            ("/noauthor/run", {"display": "Test", "author": None})
        ])
        flows = _flows_from_expose(row)
        assert len(flows) == 1
        assert flows[0].author is None
        assert flows[0].organization is None

    def test_uid_is_deterministic(self):
        row = _make_row("det", [("/det/run", {"display": "X"})])
        flows1 = _flows_from_expose(row)
        flows2 = _flows_from_expose(row)
        assert flows1[0].uid == flows2[0].uid


# ─── Integration Tests: _get_all_flows() with real DB ────────────────

@pytest.fixture
async def test_db(tmp_path):
    """Create a temporary expose.db for testing."""
    db_path = str(tmp_path / "expose.db")
    await expose_db.init_database(db_path=db_path)

    # Insert test data
    await expose_db.upsert_expose(
        name="agent_a",
        display="Agent A",
        network="Preprod",
        enabled=True,
        state="RUNNING",
        heartbeat=1234567890.0,
        bootstrap="",
        meta=_make_meta_yaml([
            ("/agent_a/run", {
                "display": "Agent Alpha",
                "description": "First agent",
                "tags": ["alpha", "test"],
            })
        ]),
        db_path=db_path,
    )
    await expose_db.upsert_expose(
        name="agent_b",
        display="Agent B",
        network="Preprod",
        enabled=True,
        state="RUNNING",
        heartbeat=1234567890.0,
        bootstrap="",
        meta=_make_meta_yaml([
            ("/agent_b/analyze", {
                "display": "Agent Beta",
                "description": "Second agent",
                "tags": ["beta", "test"],
            })
        ]),
        db_path=db_path,
    )
    await expose_db.upsert_expose(
        name="disabled_agent",
        display="Disabled",
        network=None,
        enabled=False,
        state="DEAD",
        heartbeat=0.0,
        bootstrap="",
        meta=_make_meta_yaml([
            ("/disabled_agent/run", {"display": "Should Not Appear"})
        ]),
        db_path=db_path,
    )

    # Monkey-patch the DB path
    original = expose_db.EXPOSE_DATABASE
    expose_db.EXPOSE_DATABASE = db_path
    yield db_path
    expose_db.EXPOSE_DATABASE = original


@pytest.mark.asyncio
async def test_get_all_flows(test_db):
    flows = await _get_all_flows()
    assert len(flows) == 2
    summaries = {f.summary for f in flows}
    assert summaries == {"Agent Alpha", "Agent Beta"}


@pytest.mark.asyncio
async def test_get_all_flows_query_filter(test_db):
    flows = await _get_all_flows(q="Alpha")
    assert len(flows) == 1
    assert flows[0].summary == "Agent Alpha"


@pytest.mark.asyncio
async def test_get_all_flows_no_match(test_db):
    flows = await _get_all_flows(q="nonexistent")
    assert flows == []


@pytest.mark.asyncio
async def test_get_all_flows_excludes_disabled(test_db):
    flows = await _get_all_flows()
    summaries = {f.summary for f in flows}
    assert "Should Not Appear" not in summaries


@pytest.mark.asyncio
async def test_get_all_flows_tags(test_db):
    flows = await _get_all_flows()
    all_tags = [tag for f in flows for tag in f.tags]
    assert "alpha" in all_tags
    assert "beta" in all_tags
    assert "test" in all_tags
