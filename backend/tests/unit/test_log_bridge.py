"""Unit tests for the invocation-ID log bridge (http-log-bridge spec).

The bridge has three cooperating pieces: a ContextVar carrying the invocation
context, a pure-ASGI middleware that sets it per request from the
azure_functions.* scope keys, and a handler-level logging.Filter that stamps
the worker's thread-local in the emitting thread. anyio copies contextvars
into its threadpool — the propagation the worker's threading.local lacks.
"""

import asyncio
import logging
import threading

import anyio

from app.log_bridge import (
    INVOCATION_ID_SCOPE_KEY,
    THREAD_LOCAL_SCOPE_KEY,
    InvocationContextMiddleware,
    InvocationIdFilter,
    _invocation_ctx,
    install_log_bridge,
)

logger = logging.getLogger("test.log_bridge")
logger.setLevel(logging.INFO)


class TlsCaptureHandler(logging.Handler):
    """Records the thread-local's invocation_id as seen AT EMIT TIME in the
    emitting thread — the exact read the worker's own handler performs."""

    def __init__(self, tls: threading.local) -> None:
        super().__init__()
        self.tls = tls
        self.seen: list[str | None] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.seen.append(getattr(self.tls, "invocation_id", None))


def _functions_scope(invocation_id: str, tls: object) -> dict:
    return {
        "type": "http",
        INVOCATION_ID_SCOPE_KEY: invocation_id,
        THREAD_LOCAL_SCOPE_KEY: tls,
    }


async def _noop_receive() -> dict:
    return {"type": "http.request"}


async def _noop_send(message: dict) -> None:
    pass


def test_middleware_sets_and_resets_contextvar() -> None:
    tls = threading.local()
    observed: list[object] = []

    async def downstream(scope, receive, send) -> None:
        observed.append(_invocation_ctx.get())

    middleware = InvocationContextMiddleware(downstream)

    async def run() -> None:
        await middleware(_functions_scope("inv-123", tls), _noop_receive, _noop_send)
        observed.append(_invocation_ctx.get())  # after the request: reset

    asyncio.run(run())
    assert observed == [("inv-123", tls), None]


def test_middleware_noop_without_functions_scope() -> None:
    observed: list[object] = []

    async def downstream(scope, receive, send) -> None:
        observed.append(_invocation_ctx.get())

    middleware = InvocationContextMiddleware(downstream)
    asyncio.run(middleware({"type": "http"}, _noop_receive, _noop_send))
    assert observed == [None]


def test_filter_stamps_invocation_id_from_worker_thread() -> None:
    # The real topology: middleware sets the ContextVar on the loop, the route
    # body runs in anyio's threadpool, and the filter must see the context
    # there and stamp the thread-local before the handler serializes.
    tls = threading.local()
    capture = TlsCaptureHandler(tls)
    capture.addFilter(InvocationIdFilter())
    logger.addHandler(capture)
    try:

        async def downstream(scope, receive, send) -> None:
            await anyio.to_thread.run_sync(lambda: logger.info("from threadpool"))

        middleware = InvocationContextMiddleware(downstream)
        asyncio.run(middleware(_functions_scope("inv-tp", tls), _noop_receive, _noop_send))
    finally:
        logger.removeHandler(capture)
    assert capture.seen == ["inv-tp"]


def test_filter_leaves_tls_alone_outside_requests() -> None:
    # REQ-2: on the timer path the PLATFORM stamps the thread-local; with no
    # request context active the filter must not touch it.
    tls = threading.local()
    capture = TlsCaptureHandler(tls)
    capture.addFilter(InvocationIdFilter())
    logger.addHandler(capture)
    try:

        def timer_body() -> None:
            tls.invocation_id = "platform-set"
            logger.info("from timer thread")

        # A bare thread, no event loop: the worker's own sync-call executor.
        thread = threading.Thread(target=timer_body)
        thread.start()
        thread.join()
    finally:
        logger.removeHandler(capture)
    assert capture.seen == ["platform-set"]


def test_filter_never_raises() -> None:
    # Filterer.filter has no stdlib exception guard: a raising filter would
    # surface in every logger.* call app-wide (gate F1). Broken tls -> pass.
    class BrokenTls:
        def __setattr__(self, name: str, value: object) -> None:
            raise RuntimeError("cannot stamp")

    token = _invocation_ctx.set(("inv-err", BrokenTls()))
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
        assert InvocationIdFilter().filter(record) is True
    finally:
        _invocation_ctx.reset(token)


def test_install_is_idempotent_and_handler_level() -> None:
    root = logging.getLogger()
    sentinel = logging.NullHandler()
    root.addHandler(sentinel)
    try:
        install_log_bridge()
        install_log_bridge()
        bridge_filters = [f for f in sentinel.filters if isinstance(f, InvocationIdFilter)]
        assert len(bridge_filters) == 1
    finally:
        root.removeHandler(sentinel)
