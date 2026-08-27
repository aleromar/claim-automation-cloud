"""Invocation-ID log bridge (http-log-bridge spec).

Plain-def routes run in anyio's threadpool, where the Functions Python worker
cannot attribute log records to an invocation — and the host silently discards
user logs it cannot attribute. The ASGI scope carries the worker's own
thread-local storage plus the invocation ID (the documented bridge for
user-created threads); anyio copies contextvars into its threadpool, so a
ContextVar set per request makes both reachable from the emitting thread,
where a handler-level filter stamps the ID just before the worker's handler
serializes the record.
"""

import logging
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

INVOCATION_ID_SCOPE_KEY = "azure_functions.invocation_id"
THREAD_LOCAL_SCOPE_KEY = "azure_functions.thread_local_storage"

# (invocation_id, worker thread-local storage) for the in-flight request.
_invocation_ctx: ContextVar[tuple[str, Any] | None] = ContextVar("invocation_ctx", default=None)


class InvocationContextMiddleware:
    """Pure ASGI middleware exposing the invocation context to threadpool threads.

    Inert outside the Functions host: uvicorn scopes carry no azure_functions.*
    keys (and the adapter may pass thread_local_storage=None), so dev/e2e runs
    are passthrough.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        invocation_id = scope.get(INVOCATION_ID_SCOPE_KEY)
        thread_local = scope.get(THREAD_LOCAL_SCOPE_KEY)
        if scope["type"] != "http" or not invocation_id or thread_local is None:
            await self.app(scope, receive, send)
            return
        token = _invocation_ctx.set((invocation_id, thread_local))
        try:
            await self.app(scope, receive, send)
        finally:
            _invocation_ctx.reset(token)


class InvocationIdFilter(logging.Filter):
    """Stamps the worker's thread-local invocation ID in the emitting thread.

    Only while a request context is active: on the timer path the platform
    stamps the thread-local itself and must not be clobbered (REQ-2). Never
    raises — Filterer.filter has no stdlib exception guard, so an error here
    would surface in every logger.* call app-wide.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            ctx = _invocation_ctx.get()
            if ctx is not None:
                invocation_id, thread_local = ctx
                thread_local.invocation_id = invocation_id
        except Exception:  # noqa: BLE001 — see docstring
            pass
        return True


def install_log_bridge() -> None:
    """Attach the filter to the root logger's handlers. Handler-level on
    purpose: logger-level filters do not run for propagated records. Called
    only from function_app.py — under uvicorn the bridge stays uninstalled."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, InvocationIdFilter) for f in handler.filters):
            handler.addFilter(InvocationIdFilter())
