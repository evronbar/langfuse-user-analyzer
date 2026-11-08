import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from .analyzer import Source, analyze, summarize
from .fetcher import fetch_live, normalize_rows, read_local, timestamp
from .models import ConfigError, LiveConfig, LiveError, LocalError, ReportError
from .reporter import write_pdf
from .semantic import Encoder, LocalE5, SemanticError
from .telemetry import TracedEncoder, trace_run, trace_stage


def required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value or not value.strip():
        raise ConfigError(f"missing {name}")
    return value


def credentials(
    env: Mapping[str, str], host_name: str, public_name: str, secret_name: str,
) -> tuple[str, str, str]:
    host = required(env, host_name)
    try:
        parsed = urlsplit(host)
        port = parsed.port
        valid = (
            parsed.scheme == "https" and parsed.hostname and parsed.username is None
            and parsed.password is None and parsed.path in {"", "/"}
            and re.search(r"[\x00-\x20\x7f\s?#\\]", host) is None and (port is None or port > 0)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError(f"invalid {host_name}")

    public_key = required(env, public_name)
    secret_key = required(env, secret_name)
    if not public_key.isascii() or ":" in public_key or re.search(r"[^!-~]", public_key):
        raise ConfigError(f"invalid {public_name}")
    if not secret_key.isascii() or re.search(r"[^!-~]", secret_key):
        raise ConfigError(f"invalid {secret_name}")
    return host.rstrip("/"), public_key, secret_key


def read_config(env: Mapping[str, str]) -> LiveConfig:
    host, public_key, secret_key = credentials(env, "PHA_LF_HOST", "PHA_LF_PUBLIC_KEY", "PHA_LF_SECRET_KEY")

    start = timestamp(required(env, "PHA_LF_FROM"))
    end = timestamp(required(env, "PHA_LF_TO"))
    if start is None:
        raise ConfigError("invalid PHA_LF_FROM")
    if end is None or end <= start:
        raise ConfigError("invalid PHA_LF_TO")
    return LiveConfig(host, public_key, secret_key, start, end)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse can otherwise echo arbitrary input supplied in arguments.
        raise ConfigError("invalid arguments, see --help")


def main(argv: Sequence[str] | None = None) -> int:
    parser = Parser(description="Analyze Langfuse user conversations.")
    parser.add_argument("--input-json", type=Path, help="Local UTF-8 example export.")
    parser.add_argument("--semantic-model", type=Path, help="Local E5 ONNX model directory.")
    parser.add_argument("--output", type=Path, help="Write a Hebrew PDF. Container default: /output/report.pdf.")
    parser.add_argument("--trace", action="store_true", help="Export operational spans to Langfuse.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--summary-only", action="store_true", help="Print ingestion counts only.")
    mode.add_argument("--review", action="store_true", help="Include per-turn labels without raw text or IDs.")
    try:
        args = parser.parse_args(argv)
        if args.summary_only and args.semantic_model is not None:
            raise ConfigError("--semantic-model requires analysis mode")
        if args.summary_only and args.output is not None:
            raise ConfigError("--output requires analysis mode")
        output = args.output
        if output is None and not args.summary_only and os.environ.get("DOCKER_CONTAINER") == "1":
            output = Path("/output/report.pdf")
        if output is not None and args.input_json is not None and output.resolve() == args.input_json.resolve():
            raise ConfigError("trace input and PDF destination must differ")
        source: Source = "langfuse_traces" if args.input_json is None else "local_json"
        trace_credentials = credentials(
            os.environ, "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
        ) if args.trace else None
        with trace_run(
            trace_credentials, source=source, summary_only=args.summary_only, semantic=args.semantic_model is not None,
        ) as root:
            encoder: Encoder | None = None
            if args.semantic_model is not None:
                with trace_stage(root, "load-local-e5"):
                    encoder = LocalE5(args.semantic_model)
            if args.input_json is None:
                with trace_stage(root, "fetch-langfuse-traces"):
                    rows = fetch_live(read_config(os.environ))
            else:
                with trace_stage(root, "read-local-traces"):
                    rows = read_local(args.input_json)
            with trace_stage(root, "normalize-traces"):
                batch = normalize_rows(rows)
            with trace_stage(root, "summarize-ingestion" if args.summary_only else "analyze-conversations") as stage:
                if encoder is not None and stage is not None:
                    encoder = TracedEncoder(encoder, stage)
                result = summarize(batch) if args.summary_only else analyze(
                    batch, source, include_review=args.review, encoder=encoder,
                )
            if output is not None:
                with trace_stage(root, "render-hebrew-pdf"):
                    write_pdf(result, output)
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except ConfigError as error:
        print(f"configuration: {error}", file=sys.stderr)
        return 2
    except LiveError as error:
        print(f"live source: {error}", file=sys.stderr)
        return 3
    except LocalError as error:
        print(f"local source: {error}", file=sys.stderr)
        return 4
    except ReportError as error:
        print(f"report: {error}", file=sys.stderr)
        return 5
    except SemanticError as error:
        print(f"semantic model: {error}", file=sys.stderr)
        return 6
    except Exception:
        print("unexpected application failure", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
