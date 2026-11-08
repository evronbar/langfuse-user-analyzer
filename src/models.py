from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal


class ConfigError(Exception):
    """Invalid live configuration."""


class LiveError(Exception):
    """Sanitized live source failure."""


class LocalError(Exception):
    """Sanitized local source failure."""


class ReportError(Exception):
    """Sanitized PDF rendering or output failure."""


@dataclass(frozen=True, slots=True)
class LiveConfig:
    host: str
    public_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True, repr=False)
class Trace:
    trace_id: str
    account_id: str | None
    session_id: str | None
    turn_number: int | None
    timestamp: datetime | None
    prompt: str | None
    response: str | None
    status: Literal["completed", "error", "unknown"]
    latency_seconds: Decimal | None
    cost_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class TraceBatch:
    traces: tuple[Trace, ...]
    raw_row_count: int
    skipped_row_count: int
    duplicate_row_count: int
