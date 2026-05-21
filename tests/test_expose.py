"""
Tests for the expose module - database, models, and API endpoints.
"""

import pytest
import time
import tempfile
from pathlib import Path

from tests.test_role import auth_client

from kodosumi.service.expose import db
from kodosumi.service.expose.models import (
    ExposeCreate,
    ExposeMeta,
    ExposeResponse,
    meta_to_yaml,
    create_meta_template,
    NAME_PATTERN,
)


# =============================================================================
# Database Tests
# =============================================================================

@pytest.fixture
async def test_db(tmp_path):
    """Create a temporary database for testing."""
    db_path = str(tmp_path / "test_expose.db")
    await db.init_database(db_path)
    return db_path


@pytest.mark.asyncio
async def test_db_init(tmp_path):
    """Test database initialization creates the table."""
    db_path = str(tmp_path / "expose.db")
    await db.init_database(db_path)
    assert Path(db_path).exists()


@pytest.mark.asyncio
async def test_db_get_all_empty(test_db):
    """Test getting all exposes when database is empty."""
    result = await db.get_all_exposes(test_db)
    assert result == []


@pytest.mark.asyncio
async def test_db_upsert_create(test_db):
    """Test creating a new expose item."""
    now = time.time()
    result = await db.upsert_expose(
        name="test-app",
        display="Test Application",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=now,
        bootstrap="import_path: mymodule:app",
        meta=None,
        db_path=test_db
    )

    assert result["name"] == "test-app"
    assert result["display"] == "Test Application"
    assert result["network"] == "Preprod"
    assert result["enabled"] == 1
    assert result["state"] == "DRAFT"
    assert result["bootstrap"] == "import_path: mymodule:app"
    assert result["created"] is not None
    assert result["updated"] is not None


@pytest.mark.asyncio
async def test_db_upsert_update(test_db):
    """Test updating an existing expose item."""
    now = time.time()

    # Create
    await db.upsert_expose(
        name="test-app",
        display="Original",
        network=None,
        enabled=True,
        state="DRAFT",
        heartbeat=now,
        bootstrap=None,
        meta=None,
        db_path=test_db
    )

    # Update
    result = await db.upsert_expose(
        name="test-app",
        display="Updated",
        network="Mainnet",
        enabled=False,
        state="DEAD",
        heartbeat=now + 10,
        bootstrap="import_path: updated:app",
        meta=None,
        db_path=test_db
    )

    assert result["display"] == "Updated"
    assert result["network"] == "Mainnet"
    assert result["enabled"] == 0
    assert result["state"] == "DEAD"
    assert result["bootstrap"] == "import_path: updated:app"


@pytest.mark.asyncio
async def test_db_get_expose(test_db):
    """Test getting a single expose by name."""
    now = time.time()
    await db.upsert_expose(
        name="my-app",
        display="My App",
        network="Preprod",
        enabled=True,
        state="RUNNING",
        heartbeat=now,
        bootstrap=None,
        meta=None,
        db_path=test_db
    )

    result = await db.get_expose("my-app", test_db)
    assert result is not None
    assert result["name"] == "my-app"
    assert result["display"] == "My App"


@pytest.mark.asyncio
async def test_db_get_expose_not_found(test_db):
    """Test getting a non-existent expose returns None."""
    result = await db.get_expose("nonexistent", test_db)
    assert result is None


@pytest.mark.asyncio
async def test_db_get_all_exposes(test_db):
    """Test getting all exposes returns them sorted by name."""
    now = time.time()

    for name in ["zebra", "alpha", "beta"]:
        await db.upsert_expose(
            name=name,
            display=None,
            network=None,
            enabled=True,
            state="DRAFT",
            heartbeat=now,
            bootstrap=None,
            meta=None,
            db_path=test_db
        )

    results = await db.get_all_exposes(test_db)
    assert len(results) == 3
    assert [r["name"] for r in results] == ["alpha", "beta", "zebra"]


@pytest.mark.asyncio
async def test_db_delete_expose(test_db):
    """Test deleting an expose item."""
    now = time.time()
    await db.upsert_expose(
        name="to-delete",
        display=None,
        network=None,
        enabled=True,
        state="DRAFT",
        heartbeat=now,
        bootstrap=None,
        meta=None,
        db_path=test_db
    )

    # Verify it exists
    result = await db.get_expose("to-delete", test_db)
    assert result is not None

    # Delete
    deleted = await db.delete_expose("to-delete", test_db)
    assert deleted is True

    # Verify it's gone
    result = await db.get_expose("to-delete", test_db)
    assert result is None


@pytest.mark.asyncio
async def test_db_delete_nonexistent(test_db):
    """Test deleting a non-existent expose returns False."""
    deleted = await db.delete_expose("nonexistent", test_db)
    assert deleted is False


@pytest.mark.asyncio
async def test_db_update_state(test_db):
    """Test updating only the state and heartbeat."""
    now = time.time()
    await db.upsert_expose(
        name="state-test",
        display=None,
        network=None,
        enabled=True,
        state="DRAFT",
        heartbeat=now,
        bootstrap=None,
        meta=None,
        db_path=test_db
    )

    updated = await db.update_expose_state("state-test", "RUNNING", now + 100, test_db)
    assert updated is True

    result = await db.get_expose("state-test", test_db)
    assert result["state"] == "RUNNING"
    assert result["heartbeat"] == now + 100


@pytest.mark.asyncio
async def test_db_update_meta(test_db):
    """Test updating only the meta field."""
    now = time.time()
    await db.upsert_expose(
        name="meta-test",
        display=None,
        network=None,
        enabled=True,
        state="DRAFT",
        heartbeat=now,
        bootstrap=None,
        meta=None,
        db_path=test_db
    )

    meta_yaml = "- url: /test\n  name: test-flow"
    updated = await db.update_expose_meta("meta-test", meta_yaml, test_db)
    assert updated is True

    result = await db.get_expose("meta-test", test_db)
    assert result["meta"] == meta_yaml


# =============================================================================
# Model Tests
# =============================================================================

def test_name_pattern_valid():
    """Test valid name patterns."""
    valid_names = [
        "myapp",
        "my-app",
        "my_app",
        "app123",
        "123app",
        "a",
        "1",
        "test-app-v2",
        "test_app_v2",
    ]
    for name in valid_names:
        assert NAME_PATTERN.match(name), f"Expected '{name}' to be valid"


def test_name_pattern_invalid():
    """Test invalid name patterns."""
    invalid_names = [
        "My App",       # spaces
        "MyApp",        # uppercase
        "-myapp",       # starts with hyphen
        "_myapp",       # starts with underscore
        "my.app",       # dot
        "my@app",       # special char
        "",             # empty
    ]
    for name in invalid_names:
        assert not NAME_PATTERN.match(name), f"Expected '{name}' to be invalid"


def test_expose_create_valid():
    """Test creating a valid ExposeCreate model."""
    data = ExposeCreate(
        name="test-app",
        display="Test App",
        network="Preprod",
        enabled=True,
        bootstrap="import_path: mymodule:app",
        meta=None
    )
    assert data.name == "test-app"
    assert data.network == "Preprod"


def test_expose_create_name_normalized():
    """Test that name is normalized to lowercase."""
    data = ExposeCreate(name="TestApp")
    assert data.name == "testapp"


def test_expose_create_invalid_name():
    """Test that invalid names raise validation error."""
    with pytest.raises(ValueError):
        ExposeCreate(name="Test App")  # space not allowed


def test_expose_create_invalid_bootstrap_yaml():
    """Test that invalid YAML in bootstrap raises validation error."""
    with pytest.raises(ValueError):
        ExposeCreate(
            name="test",
            bootstrap="invalid: yaml: content: ["  # malformed YAML
        )


def test_expose_create_valid_bootstrap_yaml():
    """Test that valid YAML in bootstrap passes validation."""
    data = ExposeCreate(
        name="test",
        bootstrap="""
import_path: mymodule:app
runtime_env:
  pip:
    - openai
"""
    )
    assert data.bootstrap is not None


def test_expose_create_with_meta():
    """Test creating ExposeCreate with meta entries."""
    data = ExposeCreate(
        name="test",
        meta=[
            ExposeMeta(url="/flow1", name="flow1", data="name: Flow 1"),
            ExposeMeta(url="/flow2", name="flow2", data=None),
        ]
    )
    assert len(data.meta) == 2
    assert data.meta[0].url == "/flow1"


def test_expose_create_invalid_meta_yaml():
    """Test that invalid YAML in meta data raises validation error."""
    with pytest.raises(ValueError):
        ExposeCreate(
            name="test",
            meta=[
                ExposeMeta(url="/flow1", name="flow1", data="invalid: [")
            ]
        )


def test_meta_to_yaml():
    """Test converting meta list to YAML."""
    meta = [
        ExposeMeta(url="/flow1", name="flow1", data="name: Flow 1"),
        ExposeMeta(url="/flow2", name="flow2"),
    ]
    yaml_str = meta_to_yaml(meta)
    assert yaml_str is not None
    assert "/flow1" in yaml_str
    assert "flow1" in yaml_str


def test_meta_to_yaml_none():
    """Test converting None meta returns None."""
    assert meta_to_yaml(None) is None
    assert meta_to_yaml([]) is None


def test_create_meta_template():
    """Test creating a meta template."""
    template = create_meta_template(
        url="/test/flow",
        summary="Test Flow",
        description="A test flow",
        author="test@example.com",
        organization="Test Org",
        tags=["test", "example"]
    )

    assert template.url == "/test/flow"
    # Note: meta.name was removed - identifier is derived from URL endpoint
    assert "Test Flow" in template.data
    assert "A test flow" in template.data
    assert "test@example.com" in template.data
    assert "Test Org" in template.data


def test_expose_response_from_db_row():
    """Test creating ExposeResponse from database row."""
    row = {
        "name": "test-app",
        "display": "Test App",
        "network": "Preprod",
        "enabled": 1,
        "state": "RUNNING",
        "heartbeat": 1700000000.0,
        "bootstrap": "import_path: test:app",
        "meta": "- url: /flow\n  name: test-flow",
        "created": 1699900000.0,
        "updated": 1700000000.0,
    }

    response = ExposeResponse.from_db_row(row)
    assert response.name == "test-app"
    assert response.display == "Test App"
    assert response.network == "Preprod"
    assert response.enabled is True
    assert response.state == "RUNNING"
    assert response.meta is not None
    assert len(response.meta) == 1
    assert response.meta[0].url == "/flow"


def test_expose_response_from_db_row_no_meta():
    """Test creating ExposeResponse from database row without meta."""
    row = {
        "name": "test-app",
        "display": None,
        "network": None,
        "enabled": 0,
        "state": "DRAFT",
        "heartbeat": None,
        "bootstrap": None,
        "meta": None,
        "created": 1699900000.0,
        "updated": 1700000000.0,
    }

    response = ExposeResponse.from_db_row(row)
    assert response.name == "test-app"
    assert response.enabled is False
    assert response.meta is None


# =============================================================================
# API Endpoint Tests
# =============================================================================

@pytest.mark.asyncio
async def test_api_list_exposes_empty(auth_client):
    """Test listing exposes when none exist."""
    response = await auth_client.get("/expose")
    assert response.status_code == 200
    # May have existing data from other tests, just check it's a list
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_api_create_expose(auth_client):
    """Test creating a new expose via API."""
    data = {
        "name": "api-test-app",
        "display": "API Test App",
        "network": "Preprod",
        "enabled": True,
        "bootstrap": "import_path: test:app",
        "meta": None
    }

    response = await auth_client.post("/expose", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["name"] == "api-test-app"
    assert result["display"] == "API Test App"
    assert result["network"] == "Preprod"
    assert result["enabled"] is True
    # State should be DEAD since there's no Ray Serve running
    assert result["state"] in ["DEAD", "DRAFT"]


@pytest.mark.asyncio
async def test_api_create_expose_draft(auth_client):
    """Test creating an expose without bootstrap results in DRAFT state."""
    data = {
        "name": "draft-test-app",
        "display": "Draft Test",
        "enabled": True,
        "bootstrap": None,
    }

    response = await auth_client.post("/expose", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["state"] == "DRAFT"


@pytest.mark.asyncio
async def test_api_create_expose_disabled(auth_client):
    """Test creating a disabled expose results in DEAD state."""
    data = {
        "name": "disabled-test-app",
        "display": "Disabled Test",
        "enabled": False,
        "bootstrap": "import_path: test:app",
    }

    response = await auth_client.post("/expose", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["state"] == "DEAD"


@pytest.mark.asyncio
async def test_api_create_expose_invalid_name(auth_client):
    """Test creating an expose with invalid name fails."""
    data = {
        "name": "Invalid Name",  # spaces not allowed
        "enabled": True,
    }

    response = await auth_client.post("/expose", json=data)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_api_create_expose_invalid_yaml(auth_client):
    """Test creating an expose with invalid YAML fails."""
    data = {
        "name": "yaml-fail-app",
        "bootstrap": "invalid: yaml: [",
    }

    response = await auth_client.post("/expose", json=data)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_api_get_expose(auth_client):
    """Test getting a single expose by name."""
    # First create one
    data = {
        "name": "get-test-app",
        "display": "Get Test",
        "enabled": True,
    }
    await auth_client.post("/expose", json=data)

    # Then get it
    response = await auth_client.get("/expose/get-test-app")
    assert response.status_code == 200

    result = response.json()
    assert result["name"] == "get-test-app"
    assert result["display"] == "Get Test"


@pytest.mark.asyncio
async def test_api_get_expose_not_found(auth_client):
    """Test getting a non-existent expose returns 404."""
    response = await auth_client.get("/expose/nonexistent-app-12345")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_update_expose(auth_client):
    """Test updating an existing expose."""
    # Create
    data = {
        "name": "update-test-app",
        "display": "Original",
        "enabled": True,
    }
    await auth_client.post("/expose", json=data)

    # Update
    data["display"] = "Updated"
    data["network"] = "Mainnet"
    response = await auth_client.post("/expose", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["display"] == "Updated"
    assert result["network"] == "Mainnet"


@pytest.mark.asyncio
async def test_api_delete_expose(auth_client):
    """Test deleting an expose."""
    # Create
    data = {
        "name": "delete-test-app",
        "enabled": True,
    }
    await auth_client.post("/expose", json=data)

    # Delete
    response = await auth_client.delete("/expose/delete-test-app")
    assert response.status_code == 204

    # Verify it's gone
    response = await auth_client.get("/expose/delete-test-app")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_delete_expose_not_found(auth_client):
    """Test deleting a non-existent expose returns 404."""
    response = await auth_client.delete("/expose/nonexistent-app-12345")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_create_expose_with_meta(auth_client):
    """Test creating an expose with meta entries."""
    data = {
        "name": "meta-test-app",
        "enabled": True,
        "meta": [
            {
                "url": "/flow1",
                "name": "flow1",
                "data": "name: Flow 1\ndescription: Test flow"
            }
        ]
    }

    response = await auth_client.post("/expose", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["meta"] is not None
    assert len(result["meta"]) == 1
    assert result["meta"][0]["url"] == "/flow1"
    # Note: meta.name was removed - identifier is derived from URL endpoint


@pytest.mark.asyncio
async def test_api_expose_name_case_insensitive(auth_client):
    """Test that expose names are normalized to lowercase."""
    data = {
        "name": "CamelCaseApp",
        "enabled": True,
    }

    response = await auth_client.post("/expose", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["name"] == "camelcaseapp"

    # Should be retrievable with lowercase name
    response = await auth_client.get("/expose/camelcaseapp")
    assert response.status_code == 200


# =============================================================================
# UI Endpoint Tests
# =============================================================================

@pytest.mark.asyncio
async def test_ui_main_page(auth_client):
    """Test the main expose UI page loads."""
    response = await auth_client.get("/admin/expose/")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ui_new_page(auth_client):
    """Test the new expose form page loads."""
    response = await auth_client.get("/admin/expose/new")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ui_edit_page(auth_client):
    """Test the edit expose form page loads."""
    # First create an expose
    data = {"name": "ui-edit-test", "enabled": True}
    await auth_client.post("/expose", json=data)

    response = await auth_client.get("/admin/expose/edit/ui-edit-test")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ui_edit_page_not_found(auth_client):
    """Test editing a non-existent expose returns 404."""
    response = await auth_client.get("/admin/expose/edit/nonexistent-12345")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_ui_globals_page(auth_client):
    """Test the global config page loads."""
    response = await auth_client.get("/admin/expose/globals")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ui_globals_save(auth_client):
    """Test saving global config redirects to main page."""
    config = """# Test config
proxy_location: EveryNode
http_options:
  host: 0.0.0.0
  port: 8005
"""
    response = await auth_client.post(
        "/admin/expose/globals",
        data={"config": config},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers.get("location") == "/admin/expose"


@pytest.mark.asyncio
async def test_ui_globals_save_invalid_yaml(auth_client):
    """Test saving invalid YAML shows error."""
    response = await auth_client.post(
        "/admin/expose/globals",
        data={"config": "invalid: yaml: ["},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 200  # Returns 200 with error in page
