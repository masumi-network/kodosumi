from litestar.testing import TestClient, AsyncTestClient
import pytest
from kodosumi.service.app import create_app
from kodosumi import helper
from typing import AsyncGenerator
import ray
# from tests.test_runner import init_ray

@pytest.fixture
async def http_client(tmp_path) -> AsyncGenerator:
    url = f"sqlite+aiosqlite:///{tmp_path}/admin.db"
    app = create_app(ADMIN_DATABASE=url)
    async with AsyncTestClient(app=app) as client:
        yield client

@pytest.fixture(autouse=True)
def start_ray():
    ray.init()
    yield
    ray.shutdown()


@pytest.fixture
async def auth_client(tmp_path) -> AsyncGenerator:
    url = f"sqlite+aiosqlite:///{tmp_path}/admin.db"
    app = create_app(ADMIN_DATABASE=url,
                     EXEC_DIR=str(tmp_path.joinpath("data")))
    base_url = "http://kodosumi"
    async with AsyncTestClient(app=app, base_url=base_url) as client:
        response = await client.get(
            "/login", params={"name": "admin", "password": "admin" })
        assert response.status_code == 200
        client.cookies = response.cookies
        yield client

@pytest.mark.asyncio
async def test_add_role(tmp_path):
    app = create_app(ADMIN_DATABASE=f"sqlite+aiosqlite:///{tmp_path}/admin.db")
    async with AsyncTestClient(app=app) as http_client:
        response = await http_client.get(
            "/login", params={"name": "admin", "password": "admin" })
        assert response.status_code == 200
        response = await http_client.post(
            "/role",
            json={
                "name": "user1", 
                "email": "user1@example.com", 
                "password": "user1"
            },
            cookies=response.cookies
        )
        assert response.status_code == 201


@pytest.mark.asyncio
async def test_add_role_context(http_client):
    response = await http_client.get(
        "/login", params={"name": "admin", "password": "admin" })
    assert response.status_code == 200
    response = await http_client.post(
        "/role",
        json={
            "name": "user1",
            "email": "user1@example.com",
            "password": "user1"
        },
        cookies=response.cookies
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_default_user(http_client):
    response = await http_client.get(
        "/login", params={"name": "admin", "password": "admin" })
    assert response.status_code == 200
    response = await http_client.get("/role", cookies=response.cookies)
    assert response.status_code == 200
    js = response.json()
    assert len(js) == 1
    assert js[0]["name"] == "admin"
    assert js[0]["email"] == "admin@example.com"


@pytest.mark.asyncio
async def test_config_user(tmp_path):
    app = create_app(
        ADMIN_DATABASE=f"sqlite+aiosqlite:///{tmp_path}/admin.db",
        ADMIN_EMAIL="ops@bi.com", ADMIN_PASSWORD="ops")
    async with AsyncTestClient(app=app) as client:
        response = await client.get(
            "/login", params={"name": "admin", "password": "ops" })
        assert response.status_code == 200
        response = await client.get("/role")
        assert response.status_code == 200
        js = response.json()
        assert len(js) == 1
        assert js[0]["name"] == "admin"
        assert js[0]["email"] == "ops@bi.com"


@pytest.mark.asyncio
async def test_default_password(http_client):
    response = await http_client.get(
        "/login", params={"name": "admin", "password": "admin" })
    assert response.status_code == 200
    js = response.json()


async def test_dup_role(auth_client):
    response = await auth_client.post("/role", json={"name": "user1"})
    assert response.status_code == 400
    js = response.json()
    assert js["error"] == "ValidationException"

    response = await auth_client.post(
        "/role", json={"name": "user1", "email": "user1"})
    assert response.status_code == 400
    js = response.json()
    assert js["error"] == "ValidationException"

    response = await auth_client.post(
        "/role", json={"name": "user1", "email": "user1@email.com"})
    assert response.status_code == 400
    js = response.json()
    assert js["error"] == "ValidationException"

    response = await auth_client.post(
        "/role", json={"name": "user1", 
                       "email": "user1@email.com","password": "user1"})
    assert response.status_code == 201
    js = response.json()

    response = await auth_client.post(
        "/role", json={"name": "user1", 
                       "email": "user1@email.com","password": "user1"})
    assert response.status_code == 409
    js = response.json()
    assert js["error"] == "ClientException"

    response = await auth_client.post(
        "/role", json={"name": "user2", 
                       "email": "user1@email.com","password": "user2"})
    assert response.status_code == 409
    js = response.json()
    assert js["error"] == "ClientException"

async def test_list_roles(auth_client):
    response = await auth_client.post(
        "/role", json={"name": "zzz", "email": "user3@email.com",
                         "password": "user1"})
    assert response.status_code == 201
    response = await auth_client.post(
        "/role", json={"name": "aaa", "email": "user1@email.com",
                         "password": "user1"})
    assert response.status_code == 201
    response = await auth_client.post(
        "/role", json={"name": "bbb", "email": "user2@email.com",
                         "password": "user1"})
    assert response.status_code == 201

    response = await auth_client.get("/role")
    assert response.status_code == 200
    js = response.json()
    assert [j["name"] for j in js] == ["aaa", "admin", "bbb", "zzz"]

async def test_delete_role(auth_client):
    await test_list_roles(auth_client)
    response = await auth_client.get("/role/bbb")
    assert response.status_code == 200
    js = response.json()
    rid = js["id"]
    response = await auth_client.delete(f"/role/{rid}")
    assert response.status_code == 204

    response = await auth_client.get("/role")
    assert response.status_code == 200
    js = response.json()
    assert [j["name"] for j in js] == ["aaa", "admin", "zzz"]

    response = await auth_client.get("/role/bbb")
    assert response.status_code == 404
    js = response.json()
    assert js["error"] == "NotFoundException"
    assert js["status_code"] == 404

    response = await auth_client.delete(f"/role/{rid}")
    assert response.status_code == 404

async def test_edit_role(auth_client):
    await test_list_roles(auth_client)
    response = await auth_client.get("/role/bbb")
    assert response.status_code == 200
    js = response.json()
    rid = js["id"]
    response = await auth_client.put(
        f"/role/{rid}", 
        json={"name": "ccc", "active": False})
    assert response.status_code == 200
    js = response.json()
    assert js["name"] == "ccc"
    assert not js["active"]

    response = await auth_client.get("/role/ccc")
    assert response.status_code == 200
    js = response.json()
    rid2 = js["id"]
    assert rid == rid2
    assert not js["active"]

async def test_update_password(auth_client):
    await test_list_roles(auth_client)
    response = await auth_client.get("/role/bbb")
    assert response.status_code == 200
    js = response.json()
    rid = js["id"]

    response = await auth_client.put(
        f"/role/{rid}", 
        json={"password": "bbb"})
    assert response.status_code == 200

    response = await auth_client.get(
        "/login", params={"name": "bbb", "password": "user1" })
    assert response.status_code == 401

    response = await auth_client.get(
        "/login", params={"name": "bbb", "password": "bbb" })
    assert response.status_code == 200

async def test_inactive_user(auth_client):
    await test_list_roles(auth_client)
    response = await auth_client.get("/role/bbb")
    assert response.status_code == 200
    js = response.json()
    rid = js["id"]

    response = await auth_client.put(f"/role/{rid}", json={"active": False})
    assert response.status_code == 200

    response = await auth_client.get(
        "/login", params={"name": "bbb", "password": "user1" })
    assert response.status_code == 401
