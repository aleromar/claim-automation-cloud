"""Shared error-body logging for the httpx API clients.

httpx's HTTPStatusError message carries method + URL + status but discards the
response body — the part that says WHY (Trello: "invalid value for desc";
Gmail: its structured error JSON). Failed emails get a terminal `failed` label,
so the log must suffice without reproducing. Safe to log here: both clients
authenticate via headers, never query params, so URLs and bodies carry no
secrets. One exception to keep it that way: the Gmail token endpoint carries
the refresh token + client secret in the REQUEST body — never extend this
helper to log request payloads.
"""

import logging
from typing import Final

import httpx

BODY_SNIPPET_MAX: Final = 500


def raise_for_status_logged(response: httpx.Response, logger: logging.Logger) -> None:
    if response.is_error:
        logger.warning(
            "HTTP %d from %s %s: %s",
            response.status_code,
            response.request.method,
            response.request.url,
            response.text[:BODY_SNIPPET_MAX],
        )
    response.raise_for_status()
