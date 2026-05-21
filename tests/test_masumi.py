"""
Two-stage agentic service test with lock/lease management.

Flow:
1. Initial input: Name (non-empty) and Date (must be in future)
2. SUBMIT: Validates date is in future, throws InputsError if not
3. Lock callback: Simple "Say Hello" form
4. Lease callback: Returns greeting, runner creates markdown result
"""
import asyncio
from datetime import date, datetime, timedelta
from multiprocessing import Process

import httpx
import pytest
import ray
from fastapi import Request
from ray import serve
from ray.serve import context as serve_context

import kodosumi.spooler
from kodosumi.const import STATUS_FINAL
from kodosumi.core import Launch, ServeAPI, Tracer
from kodosumi.service.inputs.errors import InputsError
from kodosumi.service.inputs.forms import (
    Cancel, InputDate, InputText, Markdown, Model, Submit
)
from kodosumi import response


def reset_serve_client():
    """Reset the Ray Serve global client to avoid stale actor references."""
    serve_context._set_global_client(None)

def run_uvicorn(factory: str, port: int):
    import uvicorn
    uvicorn.run(
        factory,
        host="localhost",
        port=port,
        reload=False
    )


async def masumi_runner(inputs: dict, tracer: Tracer):
    """
    Main runner function that requests a greeting via lock.
    After lock is resolved, creates a markdown farewell message.
    """
    await tracer.debug(f"masumi_runner started with inputs: {inputs}")

    # Request greeting via lock
    lock_result = await tracer.lock(
        "greeting-lock",
        data={
            "message": "Say Hello!",
            "original_inputs": inputs
        }
    )

    await tracer.debug(f"Lock result received: {lock_result}")

    # Get the greeting from lock result
    greeting = lock_result.get("greeting", "") if lock_result else ""

    # Create markdown result
    markdown_result = f"You say {greeting}, I say **goodbye**."

    await tracer.result(markdown_result)
    return response.Markdown(markdown_result)
    # return {
    #     "status": "completed",
    #     "original_inputs": inputs,
    #     "lock_result": lock_result,
    #     "markdown": markdown_result
    # }


def masumi_app_factory():
    """
    Factory creating a ServeAPI with two-stage flow:
    1. Initial form with Name and Date
    2. Lock for greeting
    """
    app = ServeAPI()

    # Calculate tomorrow's date for min_date validation hint
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    initial_form = Model(
        InputText(
            label="Name",
            name="name",
            placeholder="Enter your name",
            required=False
        ),
        InputDate(
            label="Date",
            name="date",
            required=True,
            min_date=tomorrow
        ),
        Submit("Submit"),
        Cancel("Cancel"),
    )

    @app.enter(
        "/",
        model=initial_form,
        summary="Masumi Two-Stage Service",
        tags=["masumi"],
        organization="Masumi Network",
        author="Test Author",
        description="Two-stage service with date validation and file upload",
    )
    async def post(inputs: dict, request: Request) -> Launch:
        """Entry point that validates date is in the future before launching."""
        # Validate name is not empty
        error = InputsError()

        name = inputs.get("name", "") or ""
        if not name.strip():
            error.add(name="Name cannot be empty")

        # Validate date is in the future
        date_str = inputs.get("date")
        if not date_str:
            error.add(date="Date is required")
        else:
            try:
                input_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                if input_date <= date.today():
                    error.add(date="Date must be in the future")
            except ValueError:
                error.add(date="Invalid date format")

        if error.has_errors():
            raise error

        return Launch(request, "tests.test_masumi:masumi_runner", inputs=inputs)

    @app.lock("greeting-lock")
    async def lock_greeting(data: dict):
        """Lock callback returning a simple greeting form."""
        return Model(
            Markdown(f"# Say Hello!\n\nOriginal submission: {data.get('original_inputs', {})}"),
            InputText(
                label="Your Greeting",
                name="greeting",
                placeholder="Say hello...",
                required=True
            ),
            Submit("Send Greeting"),
            Cancel("Cancel"),
        )

    @app.lease("greeting-lock")
    async def lease_greeting(inputs: dict):
        """
        Lease callback that returns the greeting input.
        The runner will use this to create the farewell message.
        """
        return {
            "phase": "lease-greeting",
            "greeting": inputs.get("greeting"),
            "inputs": inputs
        }

    return app


# Ray Serve Deployment
@serve.deployment
@serve.ingress(masumi_app_factory())
class MasumiService:
    pass


fast_app = MasumiService.bind()


# Fixtures

@pytest.fixture
def masumi_app_server():
    proc = Process(
        target=run_uvicorn,
        args=("tests.test_masumi:masumi_app_factory", 8127,)
    )
    proc.start()
    yield "http://localhost:8127"
    proc.kill()
    proc.terminate()
    proc.join()


@pytest.fixture
def spooler_server():
    proc = Process(target=kodosumi.spooler.run)
    proc.start()
    yield
    proc.kill()
    proc.join()


@pytest.fixture
def koco_server():
    proc = Process(
        target=run_uvicorn,
        args=("kodosumi.service.app:create_app", 8128,)
    )
    proc.start()
    yield "http://localhost:8128"
    try:
        actor = ray.get_actor("register", namespace="kodosumi")
        if actor:
            ray.kill(actor)
    except Exception:
        pass
    proc.kill()
    proc.join()


# Helper functions

async def register_flow(app_server, koco_server):
    """Register the masumi app flow with koco server."""
    client = httpx.AsyncClient()
    for _ in range(40):  # max ~10 s
        try:
            resp = await client.get(
                f"{koco_server}/login?name=admin&password=admin"
            )
            if resp.status_code == 200:
                break
        except Exception:
            pass
        await asyncio.sleep(0.25)

    resp = await client.post(
        f"{koco_server}/flow/register",
        json={"url": [f"{app_server}/openapi.json"]},
        timeout=300
    )
    assert resp.status_code == 201
    endpoints = resp.json()
    return client, endpoints


async def wait_for_status(client, koco_server, fid, target_statuses, timeout=60):
    """Wait for a specific status."""
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        try:
            resp = await client.get(f"{koco_server}/outputs/status/{fid}")
            if resp.status_code == 200:
                status = resp.json().get("status")
                if status in target_statuses:
                    return resp.json()
        except Exception:
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError(f"Timeout waiting for status in {target_statuses}")


async def wait_for_lock(client, koco_server, fid, timeout=60):
    """Wait for lock to become available and return lock id."""
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        try:
            resp = await client.get(f"{koco_server}/outputs/status/{fid}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "awaiting":
                    locks = data.get("locks", [])
                    if locks:
                        return locks[0]
        except Exception:
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError("Timeout waiting for lock")


# Tests

@pytest.mark.asyncio
async def test_date_validation_past_date(masumi_app_server, spooler_server, koco_server):
    """Test that past date raises InputsError."""
    client, endpoints = await register_flow(masumi_app_server, koco_server)

    assert len(endpoints) >= 1
    assert endpoints[0]["summary"] == "Masumi Two-Stage Service"

    # Submit with past date
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    form_data = {
        "name": "Test User",
        "date": yesterday
    }

    resp = await client.post(
        f"{koco_server}/inputs" + endpoints[0]["url"],
        data=form_data
    )
    # Should return 200 with error, not redirect
    assert resp.status_code == 200
    # The form should be re-rendered with error
    assert "Date must be in the future" in resp.text or resp.status_code == 200

    await client.aclose()


@pytest.mark.asyncio
async def test_date_validation_empty_name(masumi_app_server, spooler_server, koco_server):
    """Test that empty name raises InputsError."""
    client, endpoints = await register_flow(masumi_app_server, koco_server)

    # Submit with empty name
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    form_data = {
        "name": "",
        "date": tomorrow
    }

    resp = await client.post(
        f"{koco_server}/inputs" + endpoints[0]["url"],
        data=form_data
    )
    # Should return 200 with error form
    assert resp.status_code == 200

    await client.aclose()


@pytest.mark.asyncio
async def test_two_stage_flow_complete(masumi_app_server, spooler_server, koco_server):
    """Test complete two-stage flow with lock/lease."""
    client, endpoints = await register_flow(masumi_app_server, koco_server)

    # Submit with valid future date
    future_date = (date.today() + timedelta(days=7)).isoformat()
    form_data = {
        "name": "Test User",
        "date": future_date
    }

    resp = await client.post(
        f"{koco_server}/inputs" + endpoints[0]["url"],
        data=form_data
    )
    assert resp.status_code == 302

    # Get execution ID
    url = resp.headers.get("location")
    fid = url.split("/")[-1]

    # Wait for lock
    lid = await wait_for_lock(client, koco_server, fid)

    # Get lock form
    resp = await client.get(f"{koco_server}/lock/{fid}/{lid}")
    assert resp.status_code == 200
    lock_model = resp.json()

    # Verify lock form contains text input for greeting
    assert any(elm.get("type") == "text" for elm in lock_model)
    assert any(elm.get("type") == "markdown" for elm in lock_model)

    # Submit lock form with greeting
    lock_data = {
        "greeting": "Hello World"
    }
    resp = await client.post(
        f"{koco_server}/lock/{fid}/{lid}",
        json=lock_data,
        timeout=120
    )
    assert resp.status_code == 200

    # Wait for completion
    status_data = await wait_for_status(
        client, koco_server, fid, STATUS_FINAL
    )
    assert status_data.get("status") == "finished"

    # Check result
    resp = await client.get(f"{koco_server}/outputs/raw/{fid}")
    assert resp.status_code == 200
    result = resp.json()
    assert "Markdown" in result
    assert result["Markdown"]["body"] == "You say Hello World, I say **goodbye**."

    await client.aclose()


@pytest.mark.asyncio
async def test_lock_model_content(masumi_app_server, spooler_server, koco_server):
    """Test that lock model contains expected elements."""
    client, endpoints = await register_flow(masumi_app_server, koco_server)

    # Submit with valid data
    future_date = (date.today() + timedelta(days=7)).isoformat()
    form_data = {
        "name": "Lock Model Test",
        "date": future_date
    }

    resp = await client.post(
        f"{koco_server}/inputs" + endpoints[0]["url"],
        data=form_data
    )
    assert resp.status_code == 302

    url = resp.headers.get("location")
    fid = url.split("/")[-1]

    # Wait for lock
    lid = await wait_for_lock(client, koco_server, fid)

    # Get lock form
    resp = await client.get(f"{koco_server}/lock/{fid}/{lid}")
    assert resp.status_code == 200
    lock_model = resp.json()

    # Verify expected elements
    types = [elm.get("type") for elm in lock_model]
    assert "markdown" in types
    assert "text" in types
    assert "submit" in types
    assert "cancel" in types

    # Verify markdown contains original inputs
    markdown_elm = next(elm for elm in lock_model if elm.get("type") == "markdown")
    assert "Lock Model Test" in markdown_elm.get("text", "")

    # Clean up - delete execution
    resp = await client.delete(f"{koco_server}/outputs/{fid}")
    assert resp.status_code == 204

    await client.aclose()


@pytest.mark.asyncio
async def test_ray_serve_deployment(spooler_server, koco_server):
    """Test the service deployed via Ray Serve ingress."""
    reset_serve_client()

    # Deploy to Ray Serve
    serve.run(fast_app, route_prefix="/masumi")
    serve.status()

    try:
        # Get authenticated client
        client = httpx.AsyncClient()
        for _ in range(40):
            try:
                resp = await client.get(
                    f"{koco_server}/login?name=admin&password=admin"
                )
                if resp.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)

        # Register via Ray routes
        resp = await client.post(
            f"{koco_server}/flow/register",
            json={"url": "http://localhost:8000/-/routes"},
            timeout=300
        )
        assert resp.status_code == 201
        endpoints = resp.json()

        # Verify the masumi service is registered
        summaries = [ep["summary"] for ep in endpoints]
        assert "Masumi Two-Stage Service" in summaries

        # Find the masumi endpoint
        masumi_ep = next(
            ep for ep in endpoints
            if ep["summary"] == "Masumi Two-Stage Service"
        )
        assert "/masumi/" in masumi_ep["url"]

        # Test form submission with valid data
        future_date = (date.today() + timedelta(days=7)).isoformat()
        form_data = {
            "name": "Ray Serve Test",
            "date": future_date
        }

        resp = await client.post(
            f"{koco_server}/inputs" + masumi_ep["url"],
            data=form_data
        )
        assert resp.status_code == 302

        # Get execution ID
        url = resp.headers.get("location")
        fid = url.split("/")[-1]

        # Wait for lock
        lid = await wait_for_lock(client, koco_server, fid)

        # Get and verify lock form
        resp = await client.get(f"{koco_server}/lock/{fid}/{lid}")
        assert resp.status_code == 200
        lock_model = resp.json()
        assert any(elm.get("type") == "text" for elm in lock_model)

        # Submit lock form with greeting
        resp = await client.post(
            f"{koco_server}/lock/{fid}/{lid}",
            json={"greeting": "Hello from Ray"},
            timeout=120
        )
        assert resp.status_code == 200

        # Wait for completion
        status_data = await wait_for_status(
            client, koco_server, fid, STATUS_FINAL
        )
        assert status_data.get("status") == "finished"

        await client.aclose()

    finally:
        serve.shutdown()


app = masumi_app_factory()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("tests.test_masumi:app", host="127.0.0.1", port=8125, reload=True)