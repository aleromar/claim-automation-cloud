"""Cross-layer exception contracts (core is the meeting point: the scheduler
in app/ catches them, workloads in pipeline/ raise them — neither imports the
other)."""


class NoAccessError(Exception):
    """Wake contract (gmail-client REQ-2): a workload's preflight raises this
    to signal definitively dead credentials — run_worker classifies it as the
    `skipped_no_access` outcome. Workload-specific subclasses (GmailNoAccessError;
    5c's Trello equivalent) carry their own reasons and messages; the scheduler
    knows only this type."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or f"access unavailable: {reason}")
        self.reason = reason
