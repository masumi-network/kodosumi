"""Unit tests for /health status computation (#75/#77). Pure, no Ray."""
from kodosumi.helper import compute_health_status, _host_liveness

HEAD_RES = {"node:__internal_head__": 1.0, "CPU": 8.0}
WORKER_RES = {"CPU": 8.0}


def _node(addr, alive, resources=None):
    return {"NodeManagerAddress": addr, "Alive": alive,
            "Resources": resources if resources is not None else WORKER_RES}


def test_head_only_is_pass():
    # Single-node deployment (head only, no workers) must NOT be a failure.
    nodes = [_node("10.0.0.1", True, HEAD_RES)]
    assert compute_health_status(nodes, spooler_ok=True) == "pass"


def test_head_plus_alive_workers_is_pass():
    nodes = [_node("10.0.0.1", True, HEAD_RES),
             _node("10.0.0.2", True), _node("10.0.0.3", True)]
    assert compute_health_status(nodes, spooler_ok=True) == "pass"


def test_restarted_worker_is_pass():
    # Same host has a dead historical incarnation AND a live one → host is alive.
    nodes = [_node("10.0.0.1", True, HEAD_RES),
             _node("10.0.0.2", False),   # old incarnation (SIGTERM)
             _node("10.0.0.2", True)]    # new incarnation
    alive, dead = _host_liveness(nodes)
    assert (alive, dead) == (2, 0)
    assert compute_health_status(nodes, spooler_ok=True) == "pass"


def test_truly_dead_worker_is_warn():
    # A host whose only incarnation(s) are dead → warn (degraded, surfaced).
    nodes = [_node("10.0.0.1", True, HEAD_RES),
             _node("10.0.0.2", True),
             _node("10.0.0.3", False)]   # worker host with no live incarnation
    assert compute_health_status(nodes, spooler_ok=True) == "warn"


def test_spooler_down_is_fail():
    nodes = [_node("10.0.0.1", True, HEAD_RES), _node("10.0.0.2", True)]
    assert compute_health_status(nodes, spooler_ok=False) == "fail"


def test_zero_alive_hosts_is_fail():
    nodes = [_node("10.0.0.1", False, HEAD_RES), _node("10.0.0.2", False)]
    assert compute_health_status(nodes, spooler_ok=True) == "fail"


def test_host_liveness_collapses_historical_incarnations():
    # Mirrors the loki case: 3 live hosts + 5 dead historical incarnations of
    # those same hosts → 3 alive, 0 dead.
    nodes = [
        _node("10.2.0.4", True, HEAD_RES), _node("10.2.0.5", True), _node("10.2.0.8", True),
        _node("10.2.0.4", False), _node("10.2.0.4", False),
        _node("10.2.0.5", False), _node("10.2.0.5", False), _node("10.2.0.8", False),
    ]
    assert _host_liveness(nodes) == (3, 0)
    assert compute_health_status(nodes, spooler_ok=True) == "pass"
