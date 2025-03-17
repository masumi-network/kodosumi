from multiprocessing import Process
from ray import serve
from tests.test_exec import start_ray, start_spooler
from tests.test_role import auth_client
import tests.apps.serve_href
import asyncio

async def verify1(auth_client, prefix=""):
    resp = await auth_client.post(
        "/flow/register", 
        json={"url": f"http://localhost:8000{prefix}/openapi.json"})
    assert resp.status_code == 201

    resp = await auth_client.get("/flow")
    assert resp.status_code == 200
    js = resp.json()
    assert len(js["items"]) == 2
    assert js["total"] == 2
    assert js["items"][0]["url"] == f'/-/localhost/8000{prefix}/-/home'
    assert js["items"][1]["url"] == f'/-/localhost/8000{prefix}/-/page/post2'

    resp = await auth_client.get(f'/-/localhost/8000{prefix}/-/home')
    assert resp.status_code == 200
    cont = resp.content.decode()
    assert f'href="/-/localhost/8000{prefix}/-/"' in cont
    assert f'href="/-/localhost/8000{prefix}/-/page/1"' in cont
    assert f'href="page/1"' in cont

    resp = await auth_client.get(f'/-/localhost/8000{prefix}/-/')
    assert resp.status_code == 200

    resp = await auth_client.get(f'/-/localhost/8000{prefix}/-/page/1')
    assert resp.status_code == 200
    cont = resp.content.decode()
    assert f'href="../"' in cont
    assert f'href="./2"' in cont

    resp = await auth_client.get(f'/-/localhost/8000{prefix}/-/page/2')
    assert resp.status_code == 200
    cont = resp.content.decode()
    assert f'href="/-/localhost/8000{prefix}/-/home"' in cont
    assert f'href="/-/localhost/8000{prefix}/-/page/1"' in cont
    assert f'href="subpage/3"' in cont

    resp = await auth_client.get(f'/-/localhost/8000{prefix}/-/page/subpage/3')
    assert resp.status_code == 200
    cont = resp.content.decode()
    assert f'href="/-/localhost/8000{prefix}/-/home"' in cont
    assert f'href="/-/localhost/8000{prefix}/-/page/1"' in cont
    assert f'href="/-/localhost/8000{prefix}/-/page/subpage/3"' in cont


async def test_serve_run(start_ray, auth_client):
    serve.run(tests.apps.serve_href.fast_app)
    serve.status()
    await verify1(auth_client)
    serve.shutdown()


async def test_serve_run_prefix(start_ray, auth_client):
    serve.run(tests.apps.serve_href.fast_app, route_prefix="/entry/point")
    serve.status()
    await verify1(auth_client, prefix="/entry/point")
    serve.shutdown()

async def verify2(auth_client):

    resp = await auth_client.get("/flow")
    assert resp.status_code == 200
    js = resp.json()
    assert len(js["items"]) == 6
    assert js["total"] == 6

    actual = [(e["summary"], e["url"]) for e in js["items"]]
    expected = [
        ('Home', '/-/localhost/8000/-/home'), 
        ('Home', '/-/localhost/8000/entry/point/1/-/home'), 
        ('Home', '/-/localhost/8000/entry/point/2/-/home'), 
        ('Page Post2', '/-/localhost/8000/-/page/post2'), 
        ('Page Post2', '/-/localhost/8000/entry/point/1/-/page/post2'), 
        ('Page Post2', '/-/localhost/8000/entry/point/2/-/page/post2')
    ]

    for elm in js["items"]:
        if elm["method"] == "GET":
            meth = auth_client.get
        else:
            meth = auth_client.post
        resp = await meth(elm["url"])
        assert resp.status_code == 200

async def test_serve_run_multi(start_ray, auth_client):
    serve.run(tests.apps.serve_href.fast_app, name="app1")
    serve.run(tests.apps.serve_href.fast_app, name="app2", 
              route_prefix="/entry/point/1")
    serve.run(tests.apps.serve_href.fast_app, name="app3",
              route_prefix="/entry/point/2")
    serve.status()
    resp = await auth_client.post(
        "/flow/register", 
        json={"url": f"http://localhost:8000/openapi.json"})
    assert resp.status_code == 201
    resp = await auth_client.post(
        "/flow/register", 
        json={"url": f"http://localhost:8000/entry/point/1/openapi.json"})
    assert resp.status_code == 201

    resp = await auth_client.post(
        "/flow/register", 
        json={"url": f"http://localhost:8000/entry/point/2/openapi.json"})
    assert resp.status_code == 201

    await verify2(auth_client)
    serve.shutdown()

async def test_serve_run_routes(start_ray, auth_client):
    serve.run(tests.apps.serve_href.fast_app, name="app1")
    serve.run(tests.apps.serve_href.fast_app, name="app2", 
              route_prefix="/entry/point/1")
    serve.run(tests.apps.serve_href.fast_app, name="app3",
              route_prefix="/entry/point/2")
    serve.status()

    resp = await auth_client.post(
        "/flow/register", 
        json={"url": "http://localhost:8000/-/routes"})
    assert resp.status_code == 201

    await verify2(auth_client)

    serve.shutdown()

import uvicorn
import httpx

def _uc(port):
    uvicorn.run("tests.apps.serve_direct:create_app", 
                host="localhost", port=port, reload=False, factory=True)

async def _start_uv(port):
    proc = Process(target=_uc, args=(port,))
    proc.start()
    while True:
        try:
            resp = httpx.get(f"http://localhost:{port}")
            if resp.status_code == 200:
                break
        except:
            pass
        await asyncio.sleep(0.25)
    return proc

async def test_uvicorn(start_ray, start_spooler, auth_client):

    servers = []
    for i in range(3):
        port = 8000 + i
        servers.append(await _start_uv(port))
        resp = await auth_client.post(
            "/flow/register", 
            json={"url": f"http://localhost:{port}/openapi.json"})
    assert resp.status_code == 201
    resp = await auth_client.get("/flow?pp=100")
    assert resp.status_code == 200
    assert [(e["summary"], e["method"], e["url"]) 
            for e in resp.json()["items"]] == [
        ('Get', 'GET', '/-/localhost/8000/-/'), 
        ('Get', 'GET', '/-/localhost/8001/-/'),
        ('Get', 'GET', '/-/localhost/8002/-/'),
        ('Get End1', 'GET', '/-/localhost/8000/-/end1'),
        ('Get End1', 'GET', '/-/localhost/8001/-/end1'),
        ('Get End1', 'GET', '/-/localhost/8002/-/end1'),
        ('Get End10', 'GET', '/-/localhost/8000/-/end10'),
        ('Get End10', 'GET', '/-/localhost/8001/-/end10'),
        ('Get End10', 'GET', '/-/localhost/8002/-/end10'),
        ('Get End2', 'GET', '/-/localhost/8000/-/end2'),
        ('Get End2', 'GET', '/-/localhost/8001/-/end2'),
        ('Get End2', 'GET', '/-/localhost/8002/-/end2'),
        ('Get End3', 'GET', '/-/localhost/8000/-/end3'),
        ('Get End3', 'GET', '/-/localhost/8001/-/end3'),
        ('Get End3', 'GET', '/-/localhost/8002/-/end3'),
        ('Get End4', 'GET', '/-/localhost/8000/-/end4'),
        ('Get End4', 'GET', '/-/localhost/8001/-/end4'),
        ('Get End4', 'GET', '/-/localhost/8002/-/end4'),
        ('Get End5', 'GET', '/-/localhost/8000/-/end5'),
        ('Get End5', 'GET', '/-/localhost/8001/-/end5'),
        ('Get End5', 'GET', '/-/localhost/8002/-/end5'),
        ('Get End6', 'GET', '/-/localhost/8000/-/end6'),
        ('Get End6', 'GET', '/-/localhost/8001/-/end6'),
        ('Get End6', 'GET', '/-/localhost/8002/-/end6'),
        ('Get End7', 'GET', '/-/localhost/8000/-/end7'),
        ('Get End7', 'GET', '/-/localhost/8001/-/end7'),
        ('Get End7', 'GET', '/-/localhost/8002/-/end7'),
        ('Get End8', 'GET', '/-/localhost/8000/-/end8'),
        ('Get End8', 'GET', '/-/localhost/8001/-/end8'),
        ('Get End8', 'GET', '/-/localhost/8002/-/end8'),
        ('Get End9', 'GET', '/-/localhost/8000/-/end9'),
        ('Get End9', 'GET', '/-/localhost/8001/-/end9'),
        ('Get End9', 'GET', '/-/localhost/8002/-/end9'),
        ('Runner', 'POST', '/-/localhost/8000/-/'),
        ('Runner', 'POST', '/-/localhost/8001/-/'),
        ('Runner', 'POST', '/-/localhost/8002/-/')
    ]

    for s in servers:
        s.kill()    
