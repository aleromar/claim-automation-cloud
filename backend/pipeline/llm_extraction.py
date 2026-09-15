"""LLM implementation of the FieldExtractor seam (llm-extraction, roadmap 5a2).

Leaf module: imported only inside the `llm` branch of
`pipeline.extraction.get_field_extractor`, so the regex path never pays for
the LLM stack. Never imports `app/`.
"""

from collections.abc import Awaitable, Callable

from core.config import Settings
from pipeline.claim_data import ClaimType
from pipeline.extraction import ClaimFields

TokenProvider = Callable[[], Awaitable[str]]


class LlmFieldExtractor:
    """One structured-output call per email against the Foundry deployment
    named by Settings; regex fallback on every recognised failure (REQ-3).

    `token` is injectable so tests and cassette replay pass a static dummy
    and construct no credential."""

    def __init__(self, settings: Settings, token: TokenProvider | None = None) -> None:
        self._settings = settings
        self._token = token

    def extract(self, claim_type: ClaimType, subject: str, body: str, raw_body: str) -> ClaimFields:
        raise NotImplementedError("LlmFieldExtractor.extract lands in Task 4")

    def close(self) -> None:
        return None
