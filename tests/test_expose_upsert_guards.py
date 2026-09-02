"""
Tests for the guards of the expose upsert endpoint.

The endpoint keeps the network and the registry state of an expose stable
while agents are registered. Each refusal is checked next to a request
that has to go through.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from litestar.exceptions import ClientException

from kodosumi.service.expose.control import ExposeControl
from kodosumi.service.expose.models import ExposeCreate

UPSERT = ExposeControl.upsert_expose.fn

REGISTERED_META = yaml.dump(
    [{"url": "/flow", "data": "display: Agent\nregistrationId: reg1\n"}])


def _stored(name="expose") -> dict:
    now = time.time()
    return {
        "name": name, "display": "Expose", "network": "Preprod",
        "enabled": 1, "state": "DRAFT", "heartbeat": now,
        "bootstrap": None, "meta": REGISTERED_META,
        "created": now, "updated": 10.0,
    }


def _state() -> dict:
    return {"settings": SimpleNamespace(
        masumi_network_names=["Preprod", "Mainnet"])}


def _init():
    return patch("kodosumi.service.expose.control.db.init_database",
                 new_callable=AsyncMock)


@pytest.mark.asyncio
async def test_an_omitted_network_is_not_a_network_change():
    # db.upsert_expose keeps the stored network when the request omits it.
    # A bootstrap-only update of a registered expose changes no network,
    # so there is nothing to refuse.
    data = ExposeCreate(name="expose", etag="10.0")
    with _init(), \
            patch("kodosumi.service.expose.control.db.get_expose",
                  new_callable=AsyncMock, return_value=_stored()), \
            patch("kodosumi.service.expose.control.db.upsert_expose",
                  new_callable=AsyncMock, return_value=_stored()) as save:
        result = await UPSERT(None, data=data, state=_state())
    assert result.name == "expose"
    assert save.call_args.kwargs["network"] is None


@pytest.mark.asyncio
async def test_a_rename_onto_an_existing_name_names_the_clash():
    # The database refuses the rename either way. Without this guard the
    # operator is told to reload, which changes nothing.
    rows = {"a": _stored("a"), "b": _stored("b")}
    data = ExposeCreate(name="b", original_name="a", etag="10.0")
    with _init(), \
            patch("kodosumi.service.expose.control.db.get_expose",
                  new_callable=AsyncMock,
                  side_effect=lambda name: rows.get(name)), \
            patch("kodosumi.service.expose.control.db.upsert_expose",
                  new_callable=AsyncMock) as save:
        with pytest.raises(ClientException) as error:
            await UPSERT(None, data=data, state=_state())
    assert error.value.status_code == 409
    assert "already exists" in error.value.detail
    save.assert_not_called()
