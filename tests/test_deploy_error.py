"""
Tests for the deploy-error diagnostics in service.expose.control:
- is_failed_state()
- get_deploy_errors() / get_deploy_error()
"""

import pytest

from kodosumi.service.expose import control
from kodosumi.service.expose.control import (
    is_failed_state,
    get_deploy_error,
    get_deploy_errors,
)


# --- is_failed_state ------------------------------------------------------

@pytest.mark.parametrize("state,expected", [
    ("DEPLOY_FAILED", True),
    ("UNHEALTHY", True),
    ("DEPLOY_FAILED_PARTIAL", True),  # substring "FAIL"
    ("RUNNING", False),
    ("DRAFT", False),
    ("", False),
    (None, False),
])
def test_is_failed_state(state, expected):
    assert is_failed_state(state) is expected


# --- get_deploy_errors ----------------------------------------------------

def _write_log(tmp_path, monkeypatch, name="controller_1.log", content=""):
    serve_dir = tmp_path / "logs" / "serve"
    serve_dir.mkdir(parents=True, exist_ok=True)
    (serve_dir / name).write_text(content)
    monkeypatch.setattr(control, "RAY_SESSION_DIR", str(tmp_path))
    return serve_dir


def test_returns_none_when_log_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "RAY_SESSION_DIR", str(tmp_path / "nope"))
    assert get_deploy_errors(["simple"]) == {"simple": None}


def test_extracts_import_error_block(tmp_path, monkeypatch):
    _write_log(tmp_path, monkeypatch, content=(
        "INFO 2026-06-03 21:00:00,000 controller 1 -- starting\n"
        "ERROR 2026-06-03 21:55:34,037 controller 1 -- Exception importing application 'simple'.\n"
        "Traceback (most recent call last):\n"
        "  File \"x.py\", line 1, in <module>\n"
        "    import foo\n"
        "ModuleNotFoundError: No module named 'foo'\n"
        "INFO 2026-06-03 21:55:35,000 controller 1 -- next record\n"
    ))

    out = get_deploy_errors(["simple"])["simple"]

    assert out is not None
    assert "Exception importing application 'simple'." in out
    assert "ModuleNotFoundError: No module named 'foo'" in out
    # Stops at the next log record.
    assert "next record" not in out


def test_single_pass_multiple_names(tmp_path, monkeypatch):
    _write_log(tmp_path, monkeypatch, content=(
        "ERROR 2026-06-03 21:55:34,037 controller 1 -- Exception importing application 'alpha'.\n"
        "boom-alpha\n"
        "ERROR 2026-06-03 21:55:35,037 controller 1 -- Deploying app 'beta' failed with exception:\n"
        "boom-beta\n"
        "INFO 2026-06-03 21:55:36,000 controller 1 -- done\n"
    ))

    out = get_deploy_errors(["alpha", "beta"])

    assert "boom-alpha" in out["alpha"]
    assert "boom-beta" in out["beta"]


def test_most_recent_block_wins(tmp_path, monkeypatch):
    _write_log(tmp_path, monkeypatch, content=(
        "ERROR 2026-06-03 21:00:00,000 controller 1 -- Exception importing application 'simple'.\n"
        "old-failure\n"
        "INFO 2026-06-03 21:00:01,000 controller 1 -- retry\n"
        "ERROR 2026-06-03 22:00:00,000 controller 1 -- Exception importing application 'simple'.\n"
        "new-failure\n"
        "INFO 2026-06-03 22:00:01,000 controller 1 -- done\n"
    ))

    out = get_deploy_errors(["simple"])["simple"]

    assert "new-failure" in out
    assert "old-failure" not in out


def test_truncates_long_block(tmp_path, monkeypatch):
    head = "ERROR 2026-06-03 21:55:34,037 controller 1 -- Exception importing application 'simple'."
    body = "\n".join(f"line-{i}" for i in range(5000))
    _write_log(tmp_path, monkeypatch, content=f"{head}\n{body}\n")

    out = get_deploy_errors(["simple"], max_chars=500)["simple"]

    assert len(out) <= 500
    assert "..." in out
    # Head preserved, tail preserved.
    assert out.startswith(head[:20])
    assert "line-4999" in out


def test_no_match_returns_none(tmp_path, monkeypatch):
    _write_log(tmp_path, monkeypatch, content=(
        "INFO 2026-06-03 21:00:00,000 controller 1 -- nothing relevant here\n"
    ))
    assert get_deploy_errors(["simple"])["simple"] is None


def test_single_name_wrapper(tmp_path, monkeypatch):
    _write_log(tmp_path, monkeypatch, content=(
        "ERROR 2026-06-03 21:55:34,037 controller 1 -- Exception importing application 'simple'.\n"
        "kaboom\n"
        "INFO 2026-06-03 21:55:35,000 controller 1 -- done\n"
    ))
    assert "kaboom" in get_deploy_error("simple")
