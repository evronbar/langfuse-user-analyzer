"""Opt-in operational tracing for the analyzer pipeline."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .models import ConfigError
from .semantic import Encoder, Vector

if TYPE_CHECKING:
    from langfuse import LangfuseEmbedding, LangfuseSpan


@contextmanager
def finish_observation(observation: LangfuseSpan | LangfuseEmbedding) -> Iterator[None]:
    try:
        yield
    except BaseException:
        # Exception messages, attributes and stack traces can contain source data.
        observation.update(level="ERROR", status_message="stage failed")
        raise
    finally:
        observation.end()


@contextmanager
def trace_run(
    credentials: tuple[str, str, str] | None, *,
    source: Literal["local_json", "langfuse_traces"], summary_only: bool, semantic: bool,
) -> Iterator[LangfuseSpan | None]:
    if credentials is None:
        yield None
        return

    try:
        from langfuse import Langfuse
        from opentelemetry.context import Context, attach, detach
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        raise ConfigError("install the Langfuse SDK from requirements.txt to enable --trace") from None

    base_url, public_key, secret_key = credentials
    client = Langfuse(
        base_url=base_url, public_key=public_key, secret_key=secret_key,
        timeout=5, environment="user-analyzer",
        tracer_provider=TracerProvider(
            resource=Resource({"service.name": "langfuse-user-analyzer"}), shutdown_on_exit=False,
        ),
    )
    # Start a new trace without inheriting ambient parent IDs or baggage.
    token = attach(Context())
    try:
        root = client.start_observation(
            as_type="span", name="user-analyzer.run",
            input={"source": source, "summary_only": summary_only, "semantic_enabled": semantic},
        )
        with finish_observation(root):
            yield root
            root.update(output={"status": "completed"})
    finally:
        detach(token)
        client.shutdown()


@contextmanager
def trace_stage(parent: LangfuseSpan | None, name: str) -> Iterator[LangfuseSpan | None]:
    if parent is None:
        yield None
        return
    stage = parent.start_observation(name=name, as_type="span")
    with finish_observation(stage):
        yield stage


@dataclass(frozen=True, slots=True)
class TracedEncoder:
    encoder: Encoder
    parent: LangfuseSpan

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        stage = self.parent.start_observation(
            name="embed-local-e5", as_type="embedding", model="local-e5-onnx",
        )
        with finish_observation(stage):
            return self.encoder.encode(texts)
