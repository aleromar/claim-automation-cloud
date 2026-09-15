"""LLM implementation of the FieldExtractor seam (llm-extraction, roadmap 5a2).

Leaf module: imported only inside the `llm` branch of
`pipeline.extraction.get_field_extractor`, so the regex path never pays for
the LLM stack. Never imports `app/`.

One structured-output call per email — no tools, no toolsets, no message
history. That is what makes it safe to point at attacker-controllable email
(REQ-2.6): an injected email can corrupt its own extracted fields and nothing
else. Any tool access for this agent is a new spec, not an increment.

Threading/lifecycle: one instance per run, built and closed on the run's
thread (`run_pipeline`'s `finally`); the run lease serialises runs. Module
state is immutable constants only (P12): the two output models, the prompt
and its hash, the delimiters.
"""

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, Final

import openai
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncOpenAI
from pydantic import BaseModel, BeforeValidator, ConfigDict, PrivateAttr, model_validator
from pydantic_ai import Agent
from pydantic_ai.capabilities import Hooks, Instrumentation
from pydantic_ai.exceptions import (
    AgentRunError,
    ContentFilterError,
    ModelAPIError,
    UnexpectedModelBehavior,
)
from pydantic_ai.models import Model
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.output import NativeOutput
from pydantic_ai.providers.openai import OpenAIProvider

from core.config import Settings
from pipeline.claim_data import ClaimType
from pipeline.extraction import ClaimFields, ExtractorUsed, FailureClass, RegexFieldExtractor

logger = logging.getLogger(__name__)

TokenProvider = Callable[[], Awaitable[str]]

# Entra scope for Azure OpenAI / Foundry (D27's scope, proven in the probes).
COGNITIVE_SERVICES_SCOPE: Final = "https://cognitiveservices.azure.com/.default"
# Operator decisions 2026-09-14 (REQ-2.2, REQ-3.4): one attempt, no retries of
# any kind, then regex. The budget caps the ANSWER only; on this reasoning
# model an over-budget call comes back empty with finish_reason=length.
REASONING_EFFORT: Final = "minimal"
MAX_COMPLETION_TOKENS: Final = 1200
LLM_CALL_TIMEOUT_S: Final = 20.0
LLM_TRANSPORT_RETRIES: Final = 0
LLM_OUTPUT_RETRIES: Final = 0
# Agent name → the `invoke_agent llm_extraction` span (REQ-5.4 scoping).
AGENT_NAME: Final = "llm_extraction"
LOG_EVENT: Final = "llm_extraction"  # customDimensions['event'] in the REQ-5.3 KQL

# Versioned prompt, read once at import into immutable constants (REQ-2.5).
# A sibling-file read is deploy-safe: `.funcignore` excludes tests/venv/env
# files only (precedent: app/build_version.txt).
PROMPT_PATH: Final = Path(__file__).with_name("extraction_prompt.md")
# Stripped: Pydantic AI strips instructions, so PROMPT is exactly what the model sees.
PROMPT: Final[str] = PROMPT_PATH.read_text(encoding="utf-8").strip()
PROMPT_SHA: Final[str] = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:12]

# The email is wrapped in explicit delimiters in the user message (REQ-2.6);
# the prompt names the same tags.
CLAIM_TYPE_LABEL: Final = "Claim type:"
SUBJECT_OPEN: Final = "<email_subject>"
SUBJECT_CLOSE: Final = "</email_subject>"
BODY_OPEN: Final = "<email_body>"
BODY_CLOSE: Final = "</email_body>"


class ValidationOutcome(StrEnum):
    """What the plausibility validators did to the validated answer (REQ-5.3)."""

    OK = "ok"
    IMPLAUSIBLE_NULLED = "implausible_nulled"


def _num_to_str(value: Any) -> Any:
    # Pydantic v2 does not coerce int → str; strict schema makes this unreachable
    # on the wire, cheap insurance for any non-strict path (groundwork §7.3).
    return str(value) if isinstance(value, int | float) and not isinstance(value, bool) else value


def _digits_only(value: Any) -> Any:
    # Plausibility (REQ-2.3): "600000015andres" → "600000015"; no digits → None.
    if isinstance(value, str):
        return re.sub(r"\D", "", value) or None
    return value


def _plausible_nif(value: Any) -> Any:
    # Plausibility (REQ-2.3): nulled unless [A-Z0-9]+ after upper-casing. Never raises.
    if isinstance(value, str):
        upper = value.strip().upper()
        return upper if re.fullmatch(r"[A-Z0-9]+", upper) else None
    return value


PLAUSIBILITY_FIELDS: Final = ("nif", "phone_number")


class _TracksNulled(BaseModel):
    """Records which plausibility validators turned a present value into
    None, so the log line can say `implausible_nulled` (REQ-5.3). A private
    attribute: invisible to the JSON schema the model is given."""

    _nulled: tuple[str, ...] = PrivateAttr(default=())

    @model_validator(mode="wrap")
    @classmethod
    def _track_nulled(cls, data: Any, handler: Any) -> Any:
        instance = handler(data)
        if isinstance(data, dict):
            instance._nulled = tuple(
                name
                for name in PLAUSIBILITY_FIELDS
                if name in cls.model_fields
                and data.get(name) not in (None, "")
                and getattr(instance, name) is None
            )
        return instance


class SiniestroFields(_TracksNulled):
    """Strict output schema for every non-comunicación type (D-e). Module-scoped
    and never rebuilt: the provider caches the compiled grammar per schema."""

    model_config = ConfigDict(extra="forbid")

    insurance_company: str | None = None
    nif: Annotated[str | None, BeforeValidator(_plausible_nif), BeforeValidator(_num_to_str)] = None
    address: str | None = None
    phone_number: Annotated[
        str | None, BeforeValidator(_digits_only), BeforeValidator(_num_to_str)
    ] = None
    town: str | None = None
    description: str | None = None
    owner_name: str | None = None


class ComunicacionFields(_TracksNulled):
    """Strict output schema for COMUNICACION_A_COLABORADOR (D-e)."""

    model_config = ConfigDict(extra="forbid")

    observaciones: str | None = None


# Exhaustive over ClaimType (REQ-2.7) — a new member lands here automatically.
OUTPUT_MODEL_BY_TYPE: Final[dict[ClaimType, type[_TracksNulled]]] = {
    claim_type: (
        ComunicacionFields
        if claim_type is ClaimType.COMUNICACION_A_COLABORADOR
        else SiniestroFields
    )
    for claim_type in ClaimType
}


@dataclass
class CallState:
    """Per-call deps: the after_model_request hook records the finish reason
    here — it is not on the exception (Task 0a-truncation)."""

    finish_reason: str | None = None


def build_user_message(claim_type: ClaimType, subject: str, body: str) -> str:
    """The delimited user message (REQ-2.1/2.6): type stated, subject and body
    quarantined. A closing tag inside the email is neutralised so the email
    cannot end its own quarantine."""
    safe_subject = subject.replace(SUBJECT_CLOSE, "")
    safe_body = body.replace(BODY_CLOSE, "")
    return (
        f"{CLAIM_TYPE_LABEL} {claim_type.value}\n"
        f"{SUBJECT_OPEN}\n{safe_subject}\n{SUBJECT_CLOSE}\n"
        f"{BODY_OPEN}\n{safe_body}\n{BODY_CLOSE}"
    )


def _managed_identity_token() -> TokenProvider:
    # The SYNC credential (the entry.py/state_store.py pattern) behind an async
    # callable: AsyncOpenAI awaits `api_key()`. Blocking the run_sync loop for
    # the token fetch is harmless — nothing else runs on it. The aio flavour
    # needs aiohttp and binds to the first loop (Task 0a-cred) — rejected.
    sync_token = get_bearer_token_provider(DefaultAzureCredential(), COGNITIVE_SERVICES_SCOPE)

    async def token() -> str:
        return sync_token()

    return token


def build_model(
    settings: Settings, token: TokenProvider | None = None
) -> tuple[AsyncOpenAI, OpenAIChatModel]:
    """Keyless passthrough (Task 0a-auth): one AsyncOpenAI client on the
    `/openai/v1/` host, wrapped as a chat model with the decided settings.
    Pydantic AI maps `max_tokens` → `max_completion_tokens` for the OpenAI
    chat model; no temperature (rejected by the GPT-5 family on Azure)."""
    client = AsyncOpenAI(
        base_url=settings.foundry_endpoint,
        api_key=token if token is not None else _managed_identity_token(),
        max_retries=LLM_TRANSPORT_RETRIES,
    )
    model = OpenAIChatModel(
        settings.foundry_deployment,
        provider=OpenAIProvider(openai_client=client),
        settings=OpenAIChatModelSettings(
            openai_reasoning_effort=REASONING_EFFORT,
            max_tokens=MAX_COMPLETION_TOKENS,
            timeout=LLM_CALL_TIMEOUT_S,
        ),
    )
    return client, model


def classify_failure(exc: BaseException, finish_reason: str | None) -> FailureClass:
    """REQ-3.1 mapping from the Pydantic AI 2.43 exception tree as verified.
    ContentFilterError first (it is an UnexpectedModelBehavior)."""
    if isinstance(exc, ContentFilterError):
        return FailureClass.CONTENT_FILTER
    if isinstance(exc, UnexpectedModelBehavior):
        return (
            FailureClass.TRUNCATION if finish_reason == "length" else FailureClass.VALIDATION_FAILED
        )
    if isinstance(exc, ModelAPIError):
        # openai wraps httpx2 timeouts as APITimeoutError; Pydantic AI re-raises
        # the APIConnectionError as ModelAPIError with the original as __cause__.
        if isinstance(exc.__cause__, openai.APITimeoutError):
            return FailureClass.TIMEOUT
        return FailureClass.HTTP_ERROR  # ModelHTTPError (404/429/…) and connection errors
    if isinstance(exc, openai.OpenAIError):
        return FailureClass.HTTP_ERROR
    return FailureClass.OTHER


def _to_claim_fields(output: BaseModel, claim_type: ClaimType) -> ClaimFields:
    # The other branch's fields stay None (REQ-2.4); observaciones for
    # comunicación only is enforced by construction (REQ-2.3).
    return ClaimFields(**output.model_dump(), extractor_used=ExtractorUsed.LLM)


def _event_loop() -> asyncio.AbstractEventLoop:
    # The loop run_sync set on this thread (Pydantic AI's own get_event_loop
    # shape); a fresh one only if none exists — close() before any call.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


class LlmFieldExtractor:
    """One structured-output call per email against the Foundry deployment
    named by Settings; regex fallback on every recognised failure (REQ-3).

    `token` is injectable so tests and cassette replay pass a static dummy and
    construct no credential; `model` is injectable so Tier-1 tests drive the
    same code path with `FunctionModel` (then no client is owned here)."""

    def __init__(
        self,
        settings: Settings,
        token: TokenProvider | None = None,
        model: Model | None = None,
    ) -> None:
        self._settings = settings
        self._client: AsyncOpenAI | None = None
        if model is None:
            self._client, model = build_model(settings, token)
        self._model = model
        hooks: Hooks[CallState] = Hooks()

        @hooks.on.after_model_request
        def _record_finish_reason(ctx, *, request_context, response):
            ctx.deps.finish_reason = response.finish_reason
            return response

        # REQ-5.1/5.2: gen_ai spans on the D28 global TracerProvider (no-op
        # without one); prompt + answer captured when the switch is on —
        # operator decision, overriding otel REQ-7.2 for this call only. The
        # agent-run span also carries `pydantic_ai.all_messages`/`final_result`
        # (second copy, accepted at checkpoint 1).
        instrumentation = Instrumentation(
            settings=InstrumentationSettings(
                include_content=settings.llm_capture_content,
                include_binary_content=settings.llm_capture_content,
            )
        )
        self._agents: dict[type[_TracksNulled], Agent[CallState, Any]] = {
            output_model: Agent(
                model,
                name=AGENT_NAME,
                output_type=NativeOutput(output_model, strict=True),
                instructions=PROMPT,
                deps_type=CallState,
                retries={"output": LLM_OUTPUT_RETRIES},  # explicit: library default is 1
                capabilities=[hooks, instrumentation],
            )
            for output_model in (SiniestroFields, ComunicacionFields)
        }

    @property
    def client(self) -> AsyncOpenAI | None:
        return self._client

    @property
    def model(self) -> Model:
        return self._model

    def extract(self, claim_type: ClaimType, subject: str, body: str, raw_body: str) -> ClaimFields:
        try:
            output_model = OUTPUT_MODEL_BY_TYPE[claim_type]
        except (KeyError, TypeError):
            raise ValueError(f"Unknown claim type for extraction: {claim_type!r}") from None
        agent = self._agents[output_model]
        user_message = build_user_message(claim_type, subject, body)
        state = CallState()
        dims: dict[str, Any] = {
            "event": LOG_EVENT,
            "gen_ai.request.model": self._settings.foundry_deployment,
            "claim_type": claim_type.name,
            "prompt_chars": len(user_message),
            "extraction.prompt_sha": PROMPT_SHA,
            "retry_count": LLM_OUTPUT_RETRIES,
        }
        started = perf_counter()
        try:
            # The ONLY guarded region (REQ-3.3): schema selection above and the
            # mapping below run outside it, so their bugs propagate.
            result = agent.run_sync(user_message, deps=state)
        except (AgentRunError, openai.OpenAIError) as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            failure_class = classify_failure(exc, state.finish_reason)
            # Type only, never str(exc): validation errors quote the model's text.
            logger.warning(
                "%s fallback to regex: failure_class=%s exception_type=%s claim_type=%s "
                "duration_ms=%d",
                LOG_EVENT,
                failure_class,
                type(exc).__name__,
                claim_type.name,
                duration_ms,
                extra={
                    "failure_class": failure_class,
                    "exception_type": type(exc).__name__,
                    "claim_type": claim_type.name,
                    "duration_ms": duration_ms,
                },
            )
            fields = RegexFieldExtractor().extract(claim_type, subject, body, raw_body)
            used = ExtractorUsed.REGEX_FALLBACK
            dims.update(
                duration_ms=duration_ms,
                extractor_used=used,
                failure_class=failure_class,
                exception_type=type(exc).__name__,
            )
        else:
            duration_ms = int((perf_counter() - started) * 1000)
            fields = _to_claim_fields(result.output, claim_type)
            used = ExtractorUsed.LLM
            usage = result.usage
            dims.update(
                {
                    "duration_ms": duration_ms,
                    "extractor_used": used,
                    "gen_ai.usage.input_tokens": usage.input_tokens,
                    "gen_ai.usage.output_tokens": usage.output_tokens,
                    "gen_ai.usage.reasoning_tokens": usage.details.get("reasoning_tokens", 0),
                    "finish_reason": state.finish_reason,
                    "validation_outcome": (
                        ValidationOutcome.IMPLAUSIBLE_NULLED
                        if result.output._nulled
                        else ValidationOutcome.OK
                    ),
                }
            )
        logger.info(
            "%s extractor_used=%s claim_type=%s duration_ms=%d",
            LOG_EVENT,
            used,
            claim_type.name,
            duration_ms,
            extra=dims,
        )
        return fields.model_copy(update={"extractor_used": used})

    def close(self) -> None:
        """Release the httpx2 pool on the loop run_sync used — otherwise one
        leaked pool per run (Task 0a-close). No credential close: the sync
        credential holds no loop-bound resources."""
        if self._client is not None:
            _event_loop().run_until_complete(self._client.close())
