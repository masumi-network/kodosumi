"""
Tests for provide_input, the answer a buyer gives to a pending HITL lock.

Two things made a buyer's answer fail with a 500 before it reached the agent:
the handlers wrote a debug file under /srv/kodosumi/data first, and their
error branches built a ProvideInputResponse the model rejects. A wrong
answer must be a clear 400, and the buyer's answer must reach the lock.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from litestar.exceptions import HTTPException

from kodosumi.service.proxy import LockNotFound
from kodosumi.service.sumi import control, locks
from kodosumi.service.sumi.control import SumiControl
from kodosumi.service.sumi.locks import SumiLockControl
from kodosumi.service.sumi.models import (
    MIP003ProvideInputRequest, ProvideInputRequest, ProvideInputResponse)

LID = "6f334941-c7ab-4f90-b15d-c064240c850c"


def _lock(result=None):
    actor = MagicMock()
    return {"app_url": "http://agent", "result": result}, actor


def _agent_client(post_status=200):
    """HTTPXClient stand-in: the schema fetch fails, the lock POST answers."""
    schema = MagicMock(status_code=404)
    post = MagicMock(status_code=post_status)
    post.json.return_value = {"result": {"name": "Acme"}}
    client = MagicMock()
    client.get = AsyncMock(return_value=schema)
    client.post = AsyncMock(return_value=post)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=client)
    manager.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=manager), client


def _mip003(input_data):
    return MIP003ProvideInputRequest(job_id="fid1", input_data=input_data)


async def _answer_root(input_data, *, find_lock):
    """MIP-003 provide_input with the debug directory missing."""
    http, client = _agent_client()
    opener = MagicMock(side_effect=FileNotFoundError("no /srv/kodosumi/data"))
    with patch.object(control, "open", opener, create=True), \
            patch.object(control, "find_lock", find_lock), \
            patch.object(control, "HTTPXClient", http), \
            patch.object(control.ray, "get"):
        try:
            result = await SumiControl._provide_input_impl(
                MagicMock(), MagicMock(), _mip003(input_data))
        except HTTPException as exc:
            result = exc
    return result, client, opener


async def _answer_lock(*, find_lock):
    """The lock endpoint's provide_input with the debug directory missing."""
    http, client = _agent_client()
    opener = MagicMock(side_effect=FileNotFoundError("no /srv/kodosumi/data"))
    with patch.object(locks, "open", opener, create=True), \
            patch.object(locks, "find_lock", find_lock), \
            patch.object(locks, "HTTPXClient", http), \
            patch("ray.get"):
        try:
            result = await SumiLockControl.provide_input.fn(
                MagicMock(), MagicMock(), "fid1", LID,
                ProvideInputRequest(input_data={"name": "Acme"}))
        except HTTPException as exc:
            result = exc
    return result, client, opener


class TestAnswerOnTheMip003Endpoint:

    async def test_reaches_the_lock_without_a_debug_directory(self):
        lock, actor = _lock()
        result, client, opener = await _answer_root(
            {f"{LID}:name": "Acme"}, find_lock=MagicMock(
                return_value=(lock, actor)))

        assert isinstance(result, ProvideInputResponse)
        # The field arrives without the lock prefix, as the agent expects.
        client.post.assert_awaited_once_with(
            f"http://agent/_lock_/fid1/{LID}", json={"name": "Acme"},
            timeout=10.0)
        actor.lease.remote.assert_called_once_with(LID, {"name": "Acme"})
        # The buyer's answer is not written to a file on the server.
        opener.assert_not_called()

    @pytest.mark.parametrize("input_data, hint", [
        ({}, "input_data"),                         # nothing answered
        ({"name": "Acme"}, "prefixed"),             # no lock id in the key
    ])
    async def test_an_unusable_answer_is_a_400_with_the_reason(
            self, input_data, hint):
        result, client, _ = await _answer_root(
            input_data, find_lock=MagicMock(side_effect=AssertionError))

        assert isinstance(result, HTTPException)
        assert result.status_code == 400
        assert hint in result.detail
        client.post.assert_not_awaited()


class TestAnswerOnTheLockEndpoint:

    async def test_reaches_the_lock_without_a_debug_directory(self):
        lock, actor = _lock()
        result, client, opener = await _answer_lock(
            find_lock=MagicMock(return_value=(lock, actor)))

        assert isinstance(result, ProvideInputResponse)
        client.post.assert_awaited_once_with(
            f"http://agent/_lock_/fid1/{LID}", json={"name": "Acme"},
            timeout=10.0)
        actor.lease.remote.assert_called_once_with(LID, {"name": "Acme"})
        opener.assert_not_called()

    @pytest.mark.parametrize("find_lock", [
        MagicMock(side_effect=LockNotFound("fid1", None)),   # job is gone
        MagicMock(side_effect=LockNotFound("fid1", LID)),    # lock expired
        MagicMock(return_value=_lock(result={"done": True})),  # answered
    ], ids=["job gone", "lock expired", "already answered"])
    async def test_a_lock_that_cannot_take_an_answer_is_a_400(
            self, find_lock):
        result, client, _ = await _answer_lock(find_lock=find_lock)

        assert isinstance(result, HTTPException)
        assert result.status_code == 400
        client.post.assert_not_awaited()
