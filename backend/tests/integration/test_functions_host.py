"""Functions-host integration test (worker-skeleton REQ-5) — real `func start`.

Timer triggers never execute under the uvicorn dev/e2e path, and every past
host-path assumption that skipped `func start` failed live (deployment spec
Bugfix log #3-#5). Here the *host* is the system under test: indexing, the
timer binding, and the worker's wake path are exercised end-to-end, with the
proof read back from Azurite.

Requires Azurite (`make azurite`) and Azure Functions Core Tools v4; both are
hard dependencies of the suite — fail loudly, no skip logic (state-store
REQ-5.4 stance).
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableServiceClient

from core.secret_store import GOOGLE_CLIENT_SECRET, SESSION_SIGNING_KEY, FileSecretStore
from core.state_store import (
    ENABLED_ROW,
    HEARTBEAT_PARTITION,
    HEARTBEAT_ROW,
    HEARTBEAT_TABLE,
    WORKER_STATE_PARTITION,
    WORKER_STATE_TABLE,
    Heartbeat,
    HeartbeatStatus,
    StateStore,
)
from app.worker import WORKER_FUNCTION_NAME

BACKEND_ROOT = Path(__file__).parents[2]
HTTP_CATCH_ALL_FUNCTION = "http_app_func"  # the AsgiFunctionApp catch-all
# Every host start downloads the declared extension bundle; CI runners are
# ephemeral (bundle dir cached in CI, but a cold cache must fit the bound).
HOST_READY_TIMEOUT_S = 240
HEARTBEAT_TIMEOUT_S = 30
FUNC_INSTALL_HINT = (
    "Azure Functions Core Tools not found — install with: "
    "brew tap azure/functions && brew install azure-functions-core-tools@4 "
    "(hard dependency of the backend suite since the worker-skeleton feature)"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _delete_row(service: TableServiceClient, table: str, partition: str, row: str) -> None:
    try:
        service.get_table_client(table).delete_entity(partition, row)
    except ResourceNotFoundError:
        pass


@pytest.fixture(scope="module")
def store(service) -> StateStore:
    # The host runs against the real (unprefixed) dev tables in local Azurite.
    s = StateStore(service)
    s.ensure_tables()
    return s


@pytest.fixture
def clean_worker_state(store: StateStore):
    """Fresh rows before each test; try/finally restore even when a test raises —
    a leaked enabled=True would flip local dev state ON and red other suites."""
    service = store._service
    _delete_row(service, WORKER_STATE_TABLE, WORKER_STATE_PARTITION, ENABLED_ROW)
    _delete_row(service, HEARTBEAT_TABLE, HEARTBEAT_PARTITION, HEARTBEAT_ROW)
    try:
        yield
    finally:
        _delete_row(service, WORKER_STATE_TABLE, WORKER_STATE_PARTITION, ENABLED_ROW)
        _delete_row(service, HEARTBEAT_TABLE, HEARTBEAT_PARTITION, HEARTBEAT_ROW)


class FunctionsHost(NamedTuple):
    base_url: str
    log_path: Path


@pytest.fixture(scope="module")
def functions_host(tmp_path_factory, azurite_connection_string):
    """`func start` on a probed free port; yields a FunctionsHost — the console
    log is an assertion target too (log-bridge REQ-1: host-accepted user logs
    are printed there, dropped ones are not)."""
    if shutil.which("func") is None:
        pytest.fail(FUNC_INSTALL_HINT)

    # Env-injected settings (launcher-owned env convention — no local.settings.json):
    # the fail-fast trio (signing key + client secret seeded, OPERATOR_EMAIL) is
    # insurance in case the host runs the ASGI lifespan.
    secrets_path = tmp_path_factory.mktemp("host") / "secrets.json"
    seed = FileSecretStore(str(secrets_path))
    seed.set(SESSION_SIGNING_KEY, "k" * 32)
    seed.set(GOOGLE_CLIENT_SECRET, "host-test-placeholder")
    env = os.environ | {
        "AzureWebJobsStorage": azurite_connection_string,
        "FUNCTIONS_WORKER_RUNTIME": "python",
        # Pin the host's Python worker to the uv venv — a system python without
        # deps reproduces a Bugfix-#4-shaped "0 functions indexed" ghost.
        "languageWorkers__python__defaultExecutablePath": sys.executable,
        "STORAGE_CONNECTION_STRING": azurite_connection_string,
        # Pinned: a shell-exported managed_identity backend would redirect the
        # hosted worker to real Azure tables despite the connection string above.
        "TABLE_STORAGE_BACKEND": "connection_string",
        "SECRET_STORE_BACKEND": "file",
        "SECRET_STORE_FILE_PATH": str(secrets_path),
        "OPERATOR_EMAIL": "operator@example.com",
    }

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("host") / "func-start.log"
    with log_path.open("w") as log_file:
        proc = subprocess.Popen(
            ["func", "start", "--port", str(port)],
            cwd=BACKEND_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,  # Core Tools may prompt without a settings file
            start_new_session=True,  # own process group: func is a shim over host + worker
        )
        try:
            _wait_until_indexed(base_url, proc, log_path)
            yield FunctionsHost(base_url, log_path)
        finally:
            # start_new_session=True makes proc.pid the process-group ID; the
            # group can outlive the (already-reaped) leader, so signal the group
            # directly and tolerate its absence — getpgid on a reaped leader
            # raises ProcessLookupError and would mask the test's real failure.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # whole group already gone
            else:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=10)


def _indexed_functions(base_url: str) -> set[str]:
    response = httpx.get(f"{base_url}/admin/functions", timeout=5)
    if response.status_code != 200:
        return set()
    return {f["name"] for f in response.json()}


def _wait_until_indexed(base_url: str, proc: subprocess.Popen, log_path: Path) -> None:
    """Readiness = both functions indexed, not host liveness — /admin/host/status
    reports Running before the Python worker finishes indexing."""
    deadline = time.monotonic() + HOST_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"func start exited early (rc={proc.returncode}):\n{log_path.read_text()}")
        try:
            if {WORKER_FUNCTION_NAME, HTTP_CATCH_ALL_FUNCTION} <= _indexed_functions(base_url):
                return
        except httpx.HTTPError:
            pass  # host not listening yet
        time.sleep(1)
    pytest.fail(
        f"functions not indexed within {HOST_READY_TIMEOUT_S}s:\n{log_path.read_text()[-4000:]}"
    )


def _invoke_worker(base_url: str) -> datetime:
    invoke_time = datetime.now(UTC)
    response = httpx.post(
        f"{base_url}/admin/functions/{WORKER_FUNCTION_NAME}", json={"input": ""}, timeout=10
    )
    assert response.status_code == 202  # execution is async; proof comes via the table
    return invoke_time


def _wait_for_heartbeat(store: StateStore, after: datetime) -> Heartbeat:
    """Freshness matters: a stale row or a schedule-monitor catch-up wake must
    not fake a pass."""
    deadline = time.monotonic() + HEARTBEAT_TIMEOUT_S
    while time.monotonic() < deadline:
        heartbeat = store.read_heartbeat()
        if heartbeat is not None and heartbeat.at > after:
            return heartbeat
        time.sleep(0.5)
    pytest.fail(f"no fresh heartbeat within {HEARTBEAT_TIMEOUT_S}s (invoked at {after})")


def _wait_for_log_count(log_path: Path, needle: str, minimum: int, timeout_s: float = 20) -> int:
    """Poll the host console log until `needle` appears >= `minimum` times.
    Polled, not read once: worker→gRPC→host→stdout forwarding is asynchronous."""
    deadline = time.monotonic() + timeout_s
    count = 0
    while time.monotonic() < deadline:
        count = log_path.read_text().count(needle)
        if count >= minimum:
            return count
        time.sleep(0.5)
    return count


def test_host_indexes_both_functions(functions_host):
    # Regression guard for the Bugfix-#3 class (zero functions indexed).
    assert {WORKER_FUNCTION_NAME, HTTP_CATCH_ALL_FUNCTION} <= _indexed_functions(
        functions_host.base_url
    )


def test_timer_wake_disabled_writes_skipped_heartbeat(functions_host, store, clean_worker_state):
    # No enabled row at all: the missing-row fail-safe must read as OFF (D4).
    invoke_time = _invoke_worker(functions_host.base_url)
    heartbeat = _wait_for_heartbeat(store, after=invoke_time)
    assert heartbeat.status == HeartbeatStatus.SKIPPED_DISABLED


def test_timer_wake_enabled_without_gmail_creds_writes_skipped_no_access(
    functions_host, store, clean_worker_state
):
    # The host env deliberately seeds no gmail-refresh-token, so an enabled
    # wake must exit at the token preflight — this is the preflight's
    # host-level proof (gmail-client REQ-2); the RAN path's live proof is the
    # first connected wake after deploy (gate E4, operator-accepted residual).
    store.set_enabled(True)
    invoke_time = _invoke_worker(functions_host.base_url)
    heartbeat = _wait_for_heartbeat(store, after=invoke_time)
    assert heartbeat.status == HeartbeatStatus.SKIPPED_NO_ACCESS
    assert heartbeat.matched is None


def test_http_route_log_reaches_host_console(functions_host, store, clean_worker_state):
    # log-bridge REQ-1: a plain-def route's log record (emitted in anyio's
    # threadpool) must be accepted by the host — visible in its console log.
    # Without the bridge the host silently discards it (root-caused 2026-08-26).
    response = httpx.get(
        f"{functions_host.base_url}/api/auth/callback?state=bogus&code=bogus",
        follow_redirects=False,
        timeout=10,
    )
    assert response.status_code == 302  # the warning branch provably executed
    route_line = "callback rejected: invalid or expired state"
    assert _wait_for_log_count(functions_host.log_path, route_line, minimum=1) >= 1

    # log-bridge REQ-2: the timer path's platform-set attribution must survive
    # the bridge — count-based so earlier tests' worker wakes can't fake a pass.
    worker_line = "worker_run outcome="
    before = functions_host.log_path.read_text().count(worker_line)
    _invoke_worker(functions_host.base_url)
    assert _wait_for_log_count(functions_host.log_path, worker_line, minimum=before + 1) > before
