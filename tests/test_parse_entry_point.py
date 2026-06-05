"""
Tests for runner.main.parse_entry_point().

Focus: the shadowing fix where a package re-exports a name that collides with
one of its submodules (e.g. ``from .app import x as app`` in ``__init__.py``),
which broke the old attribute-walking resolution.
"""

import sys
import textwrap

import pytest

from kodosumi.runner.main import parse_entry_point


def _make_pkg(tmp_path, monkeypatch, name, files: dict):
    """Create an importable package under tmp_path and put it on sys.path."""
    pkg_dir = tmp_path / name
    pkg_dir.mkdir()
    for filename, content in files.items():
        (pkg_dir / filename).write_text(textwrap.dedent(content))
    monkeypatch.syspath_prepend(str(tmp_path))
    # Ensure a clean import even if a prior test imported the same name.
    for mod in list(sys.modules):
        if mod == name or mod.startswith(f"{name}."):
            del sys.modules[mod]


def test_resolves_submodule_attr_when_package_shadows_submodule(tmp_path, monkeypatch):
    """`pkg.app:thing` must resolve via the real submodule, not the re-export."""
    _make_pkg(tmp_path, monkeypatch, "shadowpkg", {
        # __init__ re-exports a name that shadows the `app` submodule.
        "__init__.py": "from shadowpkg.app import thing as app\n",
        "app.py": "thing = 'the_real_object'\n",
    })

    result = parse_entry_point("shadowpkg.app:thing")

    assert result == "the_real_object"


def test_resolves_colon_form(tmp_path, monkeypatch):
    _make_pkg(tmp_path, monkeypatch, "colonpkg", {
        "__init__.py": "",
        "mod.py": "app = 'bound_app'\n",
    })

    assert parse_entry_point("colonpkg.mod:app") == "bound_app"


def test_resolves_dotted_form(tmp_path, monkeypatch):
    """No colon: last dotted segment is the object."""
    _make_pkg(tmp_path, monkeypatch, "dottedpkg", {
        "__init__.py": "",
        "mod.py": "app = 'bound_app'\n",
    })

    assert parse_entry_point("dottedpkg.mod.app") == "bound_app"


def test_resolves_nested_attribute(tmp_path, monkeypatch):
    """Object part supports nested attribute access (e.g. Class.method)."""
    _make_pkg(tmp_path, monkeypatch, "nestedpkg", {
        "__init__.py": "",
        "mod.py": "class App:\n    handler = 'the_handler'\n",
    })

    assert parse_entry_point("nestedpkg.mod:App.handler") == "the_handler"


def test_raises_when_attr_missing(tmp_path, monkeypatch):
    _make_pkg(tmp_path, monkeypatch, "missingpkg", {
        "__init__.py": "",
        "mod.py": "app = 'x'\n",
    })

    with pytest.raises(AttributeError):
        parse_entry_point("missingpkg.mod:does_not_exist")
