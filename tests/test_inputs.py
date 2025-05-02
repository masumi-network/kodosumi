import pytest
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse
import asyncio
from kodosumi.service.inputs.forms import *
from kodosumi.serve import ServeAPI
from tests.test_exec import serve, start_ray, start_spooler
from tests.test_role import auth_client

def test_model_build():
    model = Model(
        InputText(label="Name", name="name", placeholder="Enter your name"),
        Checkbox(label="Active", name="active", value=False),
        Submit("Submit"),
        Cancel("Cancel"),
    )
    js = model.get_model_json()
    print(js)


app = ServeAPI()

model = Model(
    InputText(label="Name", name="name", placeholder="Enter your name"),
    Checkbox(label="Active", name="active", value=False),
    Submit("Submit"),
    Cancel("Cancel"),
)

@app.enter("/", model)
async def enter(inputs: dict) -> Response:
    return Response(content="OK")

@serve.deployment
@serve.ingress(app)
class TestModel1: pass

fast_app1 = TestModel1.bind()  # type: ignore


async def test_model_serve(start_ray, start_spooler, auth_client):
    serve.run(fast_app1)
    serve.status()
    resp = await auth_client.post(
        "/flow/register", json={"url": "http://localhost:8000/-/routes"})
    assert resp.status_code == 201
    # while True:
    #     resp = await auth_client.post(
    #         "/flow/register", json={"url": "http://localhost:8000/-/routes"})
    #     assert resp.status_code == 201
    #     js = resp.json()
    #     if js:
    #         break
    #     await asyncio.sleep(1)
    js = resp.json()
    resp = await auth_client.get(js[0]["url"])
    assert resp.status_code == 200
    expected = [
        {
            'type': 'text', 
            'name': 'name', 
            'label': 'Name', 
            'value': None, 
            'required': False, 
            'placeholder': 'Enter your name'
        }, 
        {
            'type': 'boolean', 
            'name': 'active', 
            'label': 'Active', 
            'value': False
        }, 
        {
            'type': 'submit', 
            'text': 'Submit'
        }, 
        {
            'type': 'cancel', 
            'text': 'Cancel'
        }
    ]
    assert resp.json().get("elements") == expected
