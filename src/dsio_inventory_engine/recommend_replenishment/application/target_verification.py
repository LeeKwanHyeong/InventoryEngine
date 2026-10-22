"""Ephemeral numerical replay admission, not a persisted trust flag or result cache.

Every read still verifies the sealed bytes, coverage and original bindings. Only
the already successful numerical replay of those exact bytes may be omitted.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import os
import threading


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


@dataclass
class _ReplayState:
    owner: tuple
    verified_key: tuple | None = None
    kernel: object = None


class TargetReplayVerification:
    """At most one verified graph; discarded on return, failure or cancellation."""

    def __init__(self):
        self._state = ContextVar("inventory_target_replay", default=None)

    @contextmanager
    def scope(self):
        current = self._state.get()
        if current is not None and current.owner == _owner():
            yield
            return
        state = _ReplayState(_owner())
        token = self._state.set(state)
        try:
            yield
        finally:
            state.verified_key, state.kernel = None, None
            self._state.reset(token)

    def contains(self, key, kernel):
        state = self._state.get()
        return (
            state is not None
            and state.owner == _owner()
            and state.verified_key == key
            and state.kernel is kernel
        )

    def remember(self, key, kernel):
        state = self._state.get()
        if state is not None and state.owner == _owner():
            state.verified_key, state.kernel = key, kernel


def target_verification_operation(method):
    """Nest synchronous assembly stages inside the same local target operation."""

    @wraps(method)
    def call(self, *args, **kwargs):
        with self.target._verification_scope():
            return method(self, *args, **kwargs)

    return call
