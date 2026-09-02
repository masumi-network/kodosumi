"""Regression tests for PR 88 registry metadata ownership."""

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Union
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from litestar.exceptions import (ClientException, NotAuthorizedException,
                                 NotFoundException, ValidationException)

from kodosumi.service.expose import db
from kodosumi.service.expose.boot import BootProgress, _step_update_meta
from kodosumi.service.expose.control import ExposeControl
from kodosumi.service.expose.flow_meta import (
    UpdatedFlowYaml, compose_flow_meta_update_fields,
    compose_flow_meta_updates, flow_meta_update_fields, update_flow_meta)
from kodosumi.service.expose.models import ExposeCreate, ExposeMeta

UPSERT_EXPOSE = ExposeControl.upsert_expose.fn


def test_registry_client_error_extra_reaches_the_http_response():
    app_source = Path("kodosumi/service/app.py").read_text()
    start = app_source.index("def app_exception_handler(")
    end = app_source.index("\n\nasync def provide_transaction", start)
    handler_source = app_source[start:end]

    class FakeResponse:
        def __init__(self, content, status_code):
            self.content = content
            self.status_code = status_code

    namespace = {
        "Any": Any,
        "Dict": Dict,
        "Union": Union,
        "Template": object,
        "Response": FakeResponse,
        "Request": object,
        "ClientException": ClientException,
        "NotFoundException": NotFoundException,
        "NotAuthorizedException": NotAuthorizedException,
        "ValidationException": ValidationException,
        "Redirect": MagicMock(),
        "TOKEN_KEY": "token",
        "HTTP_500_INTERNAL_SERVER_ERROR": 500,
        "helper": SimpleNamespace(wants=lambda _request: False),
        "logger": MagicMock(),
        "traceback": __import__("traceback"),
    }
    exec(handler_source, namespace)
    request = SimpleNamespace(url=SimpleNamespace(path="/registry"))
    error = ClientException(
        detail="request failed",
        status_code=502,
        extra={"updatedYaml": "state: failed\n", "updatedEtag": "12"},
    )

    response = namespace["app_exception_handler"](request, error)

    assert response.status_code == 502
    assert response.content["detail"] == "request failed"
    assert response.content["extra"] == error.extra


@pytest.mark.asyncio
async def test_boot_structure_update_advances_the_form_etag():
    row = {"name": "expose", "updated": 10.0, "meta": "[]"}
    save = AsyncMock(return_value=True)
    flow = SimpleNamespace(
        path="/expose/flow",
        summary="Flow",
        description="",
        tags=[],
        author=None,
        organization=None,
    )
    flow_statuses = {
        "expose": [SimpleNamespace(
            flow=flow, state="alive", checked_at=time.time())],
    }
    with (
        patch(
            "kodosumi.service.expose.boot.db.get_expose",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "kodosumi.service.expose.boot.get_existing_meta",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "kodosumi.service.expose.boot.db.update_expose_meta",
            save,
        ),
    ):
        async for _message in _step_update_meta(
            "http://localhost:3370", None, flow_statuses, BootProgress()
        ):
            pass

    assert save.await_args.kwargs["updated"] > row["updated"]


def _row(data: dict, updated: float = 10.0) -> dict:
    meta = [{"url": "/flow", "data": yaml.dump(data, sort_keys=False)}]
    return {"name": "expose", "meta": yaml.dump(meta), "updated": updated}


def test_noncontiguous_writes_do_not_hide_an_interleaved_flow_update():
    first = UpdatedFlowYaml("state: intent\n", 10.0, 11.0)
    last = UpdatedFlowYaml("state: requested\n", 12.0, 13.0)

    assert compose_flow_meta_updates(first, last).previous_etag == "12.0"
    fields = compose_flow_meta_update_fields(
        flow_meta_update_fields(first), flow_meta_update_fields(last))
    assert fields["previousEtag"] == "12.0"


@pytest.mark.asyncio
async def test_stale_base_keeps_fresh_registry_fields_and_user_edits():
    fresh = _row({
        "display": "saved",
        "agentIdentifier": "v2-agent",
        "registrationId": "v2-reg",
        "paymentSourceType": "Web3CardanoV2",
        "supportedPaymentSourceIndex": 0,
        "previousRegistration": {"agentIdentifier": "v1-agent"},
    })
    stale_browser_yaml = yaml.dump({
        "display": "browser edit",
        "agentIdentifier": "v1-agent",
        "registrationId": "v1-reg",
        "paymentSourceType": "Web3CardanoV1",
        "pendingMigration": {"registrationId": "stale-migration"},
    })

    with (
        patch(
            "kodosumi.service.expose.flow_meta.db.get_expose",
            new_callable=AsyncMock,
            return_value=fresh,
        ),
        patch(
            "kodosumi.service.expose.flow_meta.db.update_expose_meta",
            new_callable=AsyncMock,
        ) as write,
    ):
        updated = await update_flow_meta(
            fresh,
            "expose",
            "/flow",
            {"migrationError": "new error"},
            base_data=stale_browser_yaml,
            base_etag=fresh["updated"],
        )

    parsed = yaml.safe_load(updated)
    assert parsed["display"] == "browser edit"
    assert parsed["agentIdentifier"] == "v2-agent"
    assert parsed["registrationId"] == "v2-reg"
    assert parsed["paymentSourceType"] == "Web3CardanoV2"
    assert parsed["supportedPaymentSourceIndex"] == 0
    assert parsed["previousRegistration"] == {"agentIdentifier": "v1-agent"}
    assert "pendingMigration" not in parsed
    assert parsed["migrationError"] == "new error"
    assert write.call_args.kwargs["updated"] > fresh["updated"]
    assert updated.etag == str(write.call_args.kwargs["updated"])
    assert updated.previous_etag == str(fresh["updated"])


@pytest.mark.asyncio
async def test_expected_registry_value_blocks_a_stale_write():
    fresh = _row({
        "registrationId": "new-reg",
        "agentIdentifier": "new-agent",
    })
    with (
        patch(
            "kodosumi.service.expose.flow_meta.db.get_expose",
            new_callable=AsyncMock,
            return_value=fresh,
        ),
        patch(
            "kodosumi.service.expose.flow_meta.db.update_expose_meta",
            new_callable=AsyncMock,
        ) as write,
    ):
        updated = await update_flow_meta(
            fresh,
            "expose",
            "/flow",
            {"agentIdentifier": "stale-agent"},
            expected={"registrationId": "old-reg"},
        )

    assert updated is None
    write.assert_not_called()


@pytest.mark.asyncio
async def test_registry_write_rejects_an_expose_network_change():
    fresh = _row({"agentIdentifier": "agent"})
    fresh["network"] = "Mainnet"
    with (
        patch(
            "kodosumi.service.expose.flow_meta.db.get_expose",
            new_callable=AsyncMock,
            return_value=fresh,
        ),
        patch(
            "kodosumi.service.expose.flow_meta.db.update_expose_meta",
            new_callable=AsyncMock,
        ) as write,
    ):
        updated = await update_flow_meta(
            fresh,
            "expose",
            "/flow",
            {"registrationId": "new-reg"},
            expected_network="Preprod",
        )

    assert updated is None
    write.assert_not_called()


@pytest.mark.asyncio
async def test_expose_network_cannot_change_during_a_registry_lifecycle():
    current = _row({
        "agentIdentifier": "agent",
        "registrationId": "registration",
    })
    current["network"] = "Preprod"
    data = ExposeCreate(
        name="expose",
        display="Expose",
        network="Mainnet",
        etag="10.0",
        meta=[ExposeMeta(url="/flow", data="display: Agent\n")],
    )
    settings = SimpleNamespace(
        masumi_network_names=["Preprod", "Mainnet"])
    with (
        patch(
            "kodosumi.service.expose.control.db.init_database",
            new_callable=AsyncMock,
        ),
        patch(
            "kodosumi.service.expose.control.db.get_expose",
            new_callable=AsyncMock,
            return_value=current,
        ),
        patch(
            "kodosumi.service.expose.control.db.upsert_expose",
            new_callable=AsyncMock,
        ) as save,
    ):
        with pytest.raises(ClientException) as error:
            await UPSERT_EXPOSE(
                None, data=data, state={"settings": settings})

    assert error.value.status_code == 409
    assert "Deregister all agents" in error.value.detail
    save.assert_not_called()


@pytest.mark.asyncio
async def test_registry_meta_write_can_advance_the_expose_etag(tmp_path):
    db_path = str(tmp_path / "expose.db")
    await db.init_database(db_path)
    row = await db.upsert_expose(
        name="expose",
        display="Expose",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=time.time(),
        bootstrap=None,
        meta="[]",
        db_path=db_path,
    )
    next_etag = row["updated"] + 10

    await db.update_expose_meta(
        "expose", "- url: /flow\n", db_path, updated=next_etag)

    stored = await db.get_expose("expose", db_path)
    assert stored["meta"] == "- url: /flow\n"
    assert stored["updated"] == next_etag


@pytest.mark.asyncio
async def test_stale_form_update_cannot_overwrite_registry_metadata(tmp_path):
    db_path = str(tmp_path / "expose.db")
    await db.init_database(db_path)
    original = await db.upsert_expose(
        name="expose",
        display="Expose",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=time.time(),
        bootstrap=None,
        meta="- url: /flow\n  data: original\n",
        db_path=db_path,
    )
    registry_etag = original["updated"] + 10
    registry_meta = "- url: /flow\n  data: registry-update\n"
    await db.update_expose_meta(
        "expose", registry_meta, db_path, updated=registry_etag)

    result = await db.upsert_expose(
        name="expose",
        display="Stale form",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=time.time(),
        bootstrap=None,
        meta="- url: /flow\n  data: stale-form\n",
        db_path=db_path,
        expected_updated=original["updated"],
    )

    assert result is None
    stored = await db.get_expose("expose", db_path)
    assert stored["display"] == "Expose"
    assert stored["meta"] == registry_meta
    assert stored["updated"] == registry_etag


@pytest.mark.asyncio
async def test_stale_registry_write_cannot_overwrite_a_newer_form(tmp_path):
    db_path = str(tmp_path / "expose.db")
    await db.init_database(db_path)
    original = await db.upsert_expose(
        name="expose",
        display="Expose",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=time.time(),
        bootstrap=None,
        meta="- url: /flow\n  data: original\n",
        db_path=db_path,
    )
    form = await db.upsert_expose(
        name="expose",
        display="Edited form",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=time.time(),
        bootstrap=None,
        meta="- url: /flow\n  data: form-update\n",
        db_path=db_path,
        expected_updated=original["updated"],
    )

    saved = await db.update_expose_meta(
        "expose",
        "- url: /flow\n  data: stale-registry\n",
        db_path,
        updated=form["updated"] + 1,
        expected_updated=original["updated"],
    )

    assert saved is False
    stored = await db.get_expose("expose", db_path)
    assert stored["display"] == "Edited form"
    assert stored["meta"] == "- url: /flow\n  data: form-update\n"


@pytest.mark.asyncio
async def test_registry_write_retries_against_the_newer_form_yaml():
    original = _row({"display": "old"}, updated=10.0)
    form = _row({"display": "form edit"}, updated=11.0)
    with (
        patch(
            "kodosumi.service.expose.flow_meta.db.get_expose",
            new_callable=AsyncMock,
            side_effect=[original, form],
        ),
        patch(
            "kodosumi.service.expose.flow_meta.db.update_expose_meta",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ) as write,
    ):
        updated = await update_flow_meta(
            original,
            "expose",
            "/flow",
            {"registrationId": "new-reg"},
        )

    assert yaml.safe_load(updated) == {
        "display": "form edit",
        "registrationId": "new-reg",
    }
    assert write.await_count == 2
    assert write.call_args.kwargs["expected_updated"] == 11.0


@pytest.mark.asyncio
async def test_registry_write_retries_after_background_meta_change():
    original = _row({"display": "old"}, updated=10.0)
    background = _row({"display": "health update"}, updated=10.0)
    with (
        patch(
            "kodosumi.service.expose.flow_meta.db.get_expose",
            new_callable=AsyncMock,
            side_effect=[original, background],
        ),
        patch(
            "kodosumi.service.expose.flow_meta.db.update_expose_meta",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ) as write,
    ):
        updated = await update_flow_meta(
            original,
            "expose",
            "/flow",
            {"registrationId": "new-reg"},
        )

    assert yaml.safe_load(updated) == {
        "display": "health update",
        "registrationId": "new-reg",
    }
    assert write.await_count == 2
    assert write.call_args.kwargs["expected_meta"] == background["meta"]


@pytest.mark.asyncio
async def test_stale_request_yaml_does_not_replace_a_newer_form_save():
    stale_request = _row({"display": "request edit"}, updated=10.0)
    newer_form = _row({"display": "saved later"}, updated=11.0)
    with (
        patch(
            "kodosumi.service.expose.flow_meta.db.get_expose",
            new_callable=AsyncMock,
            return_value=newer_form,
        ),
        patch(
            "kodosumi.service.expose.flow_meta.db.update_expose_meta",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        updated = await update_flow_meta(
            stale_request,
            "expose",
            "/flow",
            {"registrationId": "new-reg"},
            base_data="display: request edit\n",
            base_etag=10.0,
        )

    assert yaml.safe_load(updated) == {
        "display": "saved later",
        "registrationId": "new-reg",
    }


@pytest.mark.asyncio
async def test_registration_keeps_price_saved_while_remote_mint_waited():
    request = _row({
        "display": "Agent",
        "agentPricing": [{"pricingType": "Free"}],
    }, updated=10.0)
    saved_later = _row({
        "display": "Agent",
        "agentPricing": [{
            "pricingType": "Fixed",
            "fixedPricing": [{"amount": "2000000", "unit": "lovelace"}],
        }],
    }, updated=11.0)
    with (
        patch(
            "kodosumi.service.expose.flow_meta.db.get_expose",
            new_callable=AsyncMock,
            return_value=saved_later,
        ),
        patch(
            "kodosumi.service.expose.flow_meta.db.update_expose_meta",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        updated = await update_flow_meta(
            request,
            "expose",
            "/flow",
            {"registrationId": "new-reg"},
            base_data="display: Agent\nagentPricing:\n- pricingType: Free\n",
            base_etag=10.0,
            conditional_updates={
                "agentPricing": [{"pricingType": "Free"}],
            },
        )

    parsed = yaml.safe_load(updated)
    assert parsed["registrationId"] == "new-reg"
    assert parsed["agentPricing"][0]["pricingType"] == "Fixed"


@pytest.mark.asyncio
async def test_stale_background_meta_writer_cannot_erase_registry_data(
    tmp_path,
):
    db_path = str(tmp_path / "expose.db")
    await db.init_database(db_path)
    original = await db.upsert_expose(
        name="expose",
        display="Expose",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=time.time(),
        bootstrap=None,
        meta="- url: /flow\n  data: original\n",
        db_path=db_path,
    )
    registry_meta = "- url: /flow\n  data: registry-update\n"
    await db.update_expose_meta(
        "expose",
        registry_meta,
        db_path,
        updated=original["updated"] + 1,
        expected_updated=original["updated"],
        expected_meta=original["meta"],
    )

    saved = await db.update_expose_meta(
        "expose",
        "- url: /flow\n  data: stale-health\n",
        db_path,
        expected_updated=original["updated"],
        expected_meta=original["meta"],
    )

    assert saved is False
    stored = await db.get_expose("expose", db_path)
    assert stored["meta"] == registry_meta


@pytest.mark.asyncio
async def test_null_meta_is_an_explicit_cas_value(tmp_path):
    db_path = str(tmp_path / "expose.db")
    await db.init_database(db_path)
    original = await db.upsert_expose(
        name="expose",
        display="Expose",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=time.time(),
        bootstrap=None,
        meta=None,
        db_path=db_path,
    )
    await db.update_expose_meta(
        "expose", "newer meta", db_path,
        expected_updated=original["updated"],
        expected_meta=None,
    )

    saved = await db.update_expose_meta(
        "expose", "stale meta", db_path,
        expected_updated=original["updated"],
        expected_meta=None,
    )

    assert saved is False
    stored = await db.get_expose("expose", db_path)
    assert stored["meta"] == "newer meta"


@pytest.mark.asyncio
async def test_stale_rename_keeps_the_registry_update(tmp_path):
    db_path = str(tmp_path / "expose.db")
    await db.init_database(db_path)
    original = await db.upsert_expose(
        name="expose",
        display="Expose",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=time.time(),
        bootstrap=None,
        meta="original meta",
        db_path=db_path,
    )
    await db.update_expose_meta(
        "expose", "registry meta", db_path,
        updated=original["updated"] + 1,
    )

    renamed = await db.upsert_expose(
        name="renamed",
        display="Renamed",
        network="Preprod",
        enabled=True,
        state="DRAFT",
        heartbeat=time.time(),
        bootstrap=None,
        meta="stale form meta",
        db_path=db_path,
        expected_updated=original["updated"],
        original_name="expose",
    )

    assert renamed is None
    assert await db.get_expose("renamed", db_path) is None
    stored = await db.get_expose("expose", db_path)
    assert stored["meta"] == "registry meta"


@pytest.mark.asyncio
async def test_rename_target_conflict_keeps_the_source(tmp_path):
    db_path = str(tmp_path / "expose.db")
    await db.init_database(db_path)
    source = await db.upsert_expose(
        name="source", display="Source", network="Preprod",
        enabled=True, state="DRAFT", heartbeat=time.time(),
        bootstrap=None, meta="source meta", db_path=db_path,
    )
    await db.upsert_expose(
        name="target", display="Target", network="Preprod",
        enabled=True, state="DRAFT", heartbeat=time.time(),
        bootstrap=None, meta="target meta", db_path=db_path,
    )

    renamed = await db.upsert_expose(
        name="target", display="Source", network="Preprod",
        enabled=True, state="DRAFT", heartbeat=time.time(),
        bootstrap=None, meta="source meta", db_path=db_path,
        expected_updated=source["updated"], original_name="source",
    )

    assert renamed is None
    assert (await db.get_expose("source", db_path))["meta"] == "source meta"
    assert (await db.get_expose("target", db_path))["meta"] == "target meta"
