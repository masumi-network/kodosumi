"""
Tests for the named locks the registry endpoints serialise their writes on.

An asyncio.Lock binds to the loop that first acquires it, and these locks
live in a module level table that outlives any one loop, so a second loop
in the same process must not inherit a lock bound to the first.
"""

import asyncio

import pytest

from kodosumi.service.expose.locks import keyed_lock


class TestKeyedLock:

    @pytest.mark.asyncio
    async def test_the_same_key_returns_the_same_lock(self):
        assert keyed_lock("a") is keyed_lock("a")

    @pytest.mark.asyncio
    async def test_different_keys_do_not_share_a_lock(self):
        assert keyed_lock("a") is not keyed_lock("b")

    @pytest.mark.asyncio
    async def test_it_serialises_two_holders(self):
        order = []

        async def hold(tag, delay):
            async with keyed_lock("shared"):
                order.append("enter-" + tag)
                await asyncio.sleep(delay)
                order.append("exit-" + tag)

        await asyncio.gather(hold("a", 0.01), hold("b", 0))
        assert order in (
            ["enter-a", "exit-a", "enter-b", "exit-b"],
            ["enter-b", "exit-b", "enter-a", "exit-a"],
        )

    def test_a_second_event_loop_gets_its_own_lock(self):
        # The failure this guards is a RuntimeError from asyncio itself:
        # "is bound to a different event loop".
        async def acquire():
            async with keyed_lock("cross-loop"):
                return True

        assert asyncio.run(acquire()) is True
        assert asyncio.run(acquire()) is True

    def test_a_lock_left_locked_does_not_leak_into_the_next_loop(self):
        async def leave_locked():
            lock = keyed_lock("abandoned")
            await lock.acquire()
            return lock

        first = asyncio.run(leave_locked())
        assert first.locked()

        async def acquire_again():
            lock = keyed_lock("abandoned")
            assert lock is not first
            async with lock:
                return True

        assert asyncio.run(acquire_again()) is True
