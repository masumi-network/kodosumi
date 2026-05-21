"""
Tests for proxy route matching logic using expose.db flows.
"""
import pytest
import yaml

from kodosumi.service.flow import _flows_from_expose


def _make_row(name, urls):
    """Build a fake expose row with multiple flow URLs."""
    meta_list = []
    for url, display in urls:
        meta_list.append({
            "url": url,
            "data": yaml.dump({"display": display}),
            "enabled": True,
            "state": "alive",
            "heartbeat": 0.0,
        })
    return {
        "name": name,
        "display": name,
        "network": "Preprod",
        "enabled": 1,
        "state": "RUNNING",
        "heartbeat": 0.0,
        "bootstrap": "",
        "meta": yaml.dump(meta_list),
        "created": 0.0,
        "updated": 0.0,
    }


def _match_route(flows, lookup):
    """Simulate proxy route matching logic from proxy.py."""
    flows_sorted = sorted(flows, key=lambda c: c.url, reverse=True)
    lookup = lookup.rstrip("/")
    for ep in flows_sorted:
        url = ep.url.rstrip("/")
        if lookup.startswith(url) or lookup.startswith(url + "/"):
            return ep
    return None


class TestProxyRouting:

    def test_simple_match(self):
        row = _make_row("myapp", [("/myapp/run", "Run Agent")])
        flows = _flows_from_expose(row, ray_serve_address="http://localhost:8005")
        ep = _match_route(flows, "/-/myapp/myapp/run")
        assert ep is not None
        assert ep.summary == "Run Agent"

    def test_no_match(self):
        row = _make_row("myapp", [("/myapp/run", "Run Agent")])
        flows = _flows_from_expose(row, ray_serve_address="http://localhost:8005")
        ep = _match_route(flows, "/-/otherapp/something")
        assert ep is None

    def test_longest_prefix_wins(self):
        row = _make_row("multi", [
            ("/multi/analyze", "Analyze"),
            ("/multi/analyze/deep", "Deep Analyze"),
        ])
        flows = _flows_from_expose(row, ray_serve_address="http://localhost:8005")
        # Deep path should match the longer prefix
        ep = _match_route(flows, "/-/multi/multi/analyze/deep")
        assert ep is not None
        assert ep.summary == "Deep Analyze"

    def test_multiple_exposes(self):
        row_a = _make_row("app_a", [("/app_a/run", "Agent A")])
        row_b = _make_row("app_b", [("/app_b/run", "Agent B")])
        flows = _flows_from_expose(row_a) + _flows_from_expose(row_b)

        ep = _match_route(flows, "/-/app_a/app_a/run")
        assert ep.summary == "Agent A"

        ep = _match_route(flows, "/-/app_b/app_b/run")
        assert ep.summary == "Agent B"

    def test_trailing_slash(self):
        row = _make_row("myapp", [("/myapp/run", "Run")])
        flows = _flows_from_expose(row, ray_serve_address="http://localhost:8005")
        ep = _match_route(flows, "/-/myapp/myapp/run/")
        assert ep is not None
        assert ep.summary == "Run"
