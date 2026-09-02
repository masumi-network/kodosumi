"""
Named asyncio locks that survive more than one event loop.

An asyncio.Lock binds to the loop that first acquires it and raises
RuntimeError on every later acquire from another one. The registry
endpoints keep their locks in module level dicts, which outlive any
single loop, so the lock has to be looked up per running loop instead.
"""

import asyncio
from typing import Dict
from weakref import WeakKeyDictionary

# One table of locks per event loop. The entry disappears with the loop,
# so a process that runs several of them leaks nothing.
_LOCKS: "WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[str, asyncio.Lock]]" \
    = WeakKeyDictionary()


def keyed_lock(key: str) -> asyncio.Lock:
    """Return the lock of `key` for the running loop, creating it once."""
    loop = asyncio.get_running_loop()
    per_loop = _LOCKS.get(loop)
    if per_loop is None:
        per_loop = _LOCKS[loop] = {}
    lock = per_loop.get(key)
    if lock is None:
        lock = per_loop[key] = asyncio.Lock()
    return lock
