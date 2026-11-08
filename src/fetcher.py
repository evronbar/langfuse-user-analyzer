import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import sleep
from typing import Literal

from .models import LiveConfig, LiveError, LocalError, Trace, TraceBatch


def read_local(path: Path) -> tuple[object, ...]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"), parse_float=Decimal)
    except (OSError, ValueError, RecursionError):
        raise LocalError("expected a readable UTF-8 JSON array") from None
    if not isinstance(value, list):
        raise LocalError("expected a JSON array")
    return tuple(value)


def object_fields(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def decoded_fields(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return object_fields(value)


def timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value
    ):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def clean_id(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def measure(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = Decimal(str(value))
    return number if number.is_finite() and number >= 0 else None


def status_of(*values: object) -> Literal["completed", "error", "unknown"]:
    for value in values:
        if isinstance(value, str):
            status = value.strip().casefold()
            if status == "completed":
                return "completed"
            if status == "error":
                return "error"
    return "unknown"


def normalize_rows(rows: Sequence[object]) -> TraceBatch:
    traces: list[Trace] = []
    seen: set[str] = set()
    skipped = duplicates = 0
    for raw in rows:
        row = object_fields(raw)
        trace_id = clean_id(row.get("id"))
        if trace_id is None:
            skipped += 1
            continue
        if trace_id in seen:
            duplicates += 1
            continue
        seen.add(trace_id)
        metadata = object_fields(row.get("metadata"))
        output = object_fields(row.get("output"))
        turn = metadata.get("turn_number")
        traces.append(Trace(
            trace_id=trace_id,
            account_id=clean_id(metadata.get("account_uuid")),
            session_id=clean_id(row.get("sessionId")),
            turn_number=turn if type(turn) is int and turn > 0 else None,
            timestamp=timestamp(row.get("timestamp")),
            prompt=text(object_fields(row.get("input")).get("user_prompt")),
            response=text(output.get("response")),
            status=status_of(metadata.get("status"), output.get("status")),
            latency_seconds=measure(row.get("latency")),
            cost_usd=measure(row.get("totalCost")),
        ))
    return TraceBatch(tuple(traces), len(rows), skipped, duplicates)


def project_live_trace(raw: object) -> dict[str, object]:
    row = object_fields(raw)
    metadata = decoded_fields(row.get("metadata"))
    input_fields = decoded_fields(row.get("input"))
    output_fields = decoded_fields(row.get("output"))
    return {
        "id": row.get("id"),
        "timestamp": row.get("timestamp"),
        "sessionId": row.get("sessionId"),
        "input": {"user_prompt": input_fields.get("user_prompt")},
        "output": {"response": output_fields.get("response"), "status": output_fields.get("status")},
        "metadata": {key: metadata.get(key) for key in ("account_uuid", "status", "turn_number")},
        "latency": row.get("latency"),
        "totalCost": row.get("totalCost"),
    }


def fetch_live(config: LiveConfig) -> tuple[object, ...]:
    # Import only in live mode so local analysis works without network dependencies.
    try:
        import requests
    except ImportError:
        raise LiveError("live mode requires the pinned dependencies") from None

    params: dict[str, str | int] = {
        "fromTimestamp": config.start.isoformat(),
        "toTimestamp": config.end.isoformat(),
        "limit": 50,
    }
    projected: list[object] = []
    with requests.Session() as session:
        session.trust_env = False
        session.auth = (config.public_key, config.secret_key)
        for page in range(1, 1001):
            for attempt in range(3):
                delay = attempt + 1
                try:
                    response = session.get(
                        config.host + "/api/public/traces",
                        params={**params, "page": page}, timeout=(5, 30), allow_redirects=False,
                    )
                except requests.exceptions.SSLError:
                    raise LiveError(f"TLS failure on page {page}") from None
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    pass
                except requests.exceptions.RequestException:
                    raise LiveError(f"request failed on page {page}") from None
                else:
                    code = response.status_code
                    if code == 200:
                        break
                    if code in {404, 405, 410}:
                        raise LiveError(f"traces endpoint unavailable (HTTP {code})")
                    if code != 429 and not 500 <= code <= 599:
                        raise LiveError(f"HTTP {code} on page {page}")
                    retry_after = response.headers.get("Retry-After", "")
                    if retry_after.isascii() and retry_after.isdecimal():
                        retry_after = retry_after.lstrip("0") or "0"
                        if len(retry_after) > 2 or int(retry_after) > 30:
                            raise LiveError(f"retry limit exceeded on page {page}")
                        delay = max(delay, int(retry_after))
                if attempt < 2:
                    sleep(delay)
            else:
                raise LiveError(f"request retries exhausted on page {page}")

            try:
                body = object_fields(response.json(parse_float=Decimal))
            except (ValueError, RecursionError):
                raise LiveError(f"invalid JSON on page {page}") from None
            rows, meta = body.get("data"), body.get("meta")
            if not isinstance(rows, list) or not isinstance(meta, dict):
                raise LiveError(f"invalid envelope on page {page}")
            current_page, total_pages = meta.get("page"), meta.get("totalPages")
            if type(current_page) is not int or current_page != page or type(total_pages) is not int or total_pages < 0:
                raise LiveError(f"invalid pagination on page {page}")
            if total_pages == 0 and (page != 1 or rows):
                raise LiveError(f"inconsistent pagination on page {page}")
            if total_pages > 1000:
                raise LiveError("pagination limit exceeded")
            projected.extend(project_live_trace(row) for row in rows)
            if page >= total_pages:
                return tuple(projected)
    raise LiveError("pagination limit exceeded")
