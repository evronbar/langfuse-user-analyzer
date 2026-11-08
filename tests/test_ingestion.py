import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.fetcher import fetch_live, normalize_rows
from src.main import main, read_config
from src.models import ConfigError, LiveError


ENV = {
    "PHA_LF_HOST": "https://example.invalid/",
    "PHA_LF_PUBLIC_KEY": "synthetic-public",
    "PHA_LF_SECRET_KEY": "synthetic-secret",
    "PHA_LF_FROM": "2025-01-01T00:00:00+02:00",
    "PHA_LF_TO": "2025-01-03T00:00:00Z",
}


def mock_http(*responses):
    session = MagicMock()
    session.get.side_effect = responses
    context = MagicMock()
    context.__enter__.return_value = session
    client = SimpleNamespace(Session=lambda: context)
    return session, patch.dict("sys.modules", {"requests": client})


def response(status, body=None, headers=None):
    result = MagicMock(status_code=status, headers=headers or {})
    result.json.return_value = body
    return result


class IngestionTests(unittest.TestCase):
    def test_config_rejects_unsafe_values_without_echoing_them(self):
        for name, value in (
            ("PHA_LF_HOST", "https://secret@example.invalid"),
            ("PHA_LF_HOST", "https://example.invalid/?secret"),
            ("PHA_LF_FROM", "2025-01-01T00:00:00+02:99"),
            ("PHA_LF_TO", ENV["PHA_LF_FROM"]),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaisesRegex(ConfigError, name) as failure:
                    read_config({**ENV, name: value})
                self.assertNotIn(value, str(failure.exception))

    def test_row_normalization_keeps_errors_and_distinguishes_duplicate_ids(self):
        first = {"id": "one", "metadata": {"status": "error", "turn_number": True},
                 "output": {"response": "", "status": "completed"}, "latency": False}
        batch = normalize_rows([first, first, {**first, "id": "two"}, {"id": ""}, None])
        self.assertEqual((len(batch.traces), batch.skipped_row_count, batch.duplicate_row_count), (2, 2, 1))
        trace = batch.traces[0]
        self.assertEqual((trace.status, trace.response), ("error", ""))
        self.assertIsNone(trace.turn_number)
        self.assertIsNone(trace.latency_seconds)

    def test_live_paginates_traces_and_preserves_cost_and_chat_fields(self):
        trace = {"id": "example", "timestamp": "2025-01-01T00:00:00Z", "sessionId": "session",
                "input": '{"user_prompt":"question"}',
                "output": {"response": "answer"}, "metadata": {"account_uuid": "account", "turn_number": 2},
                "latency": 3.5, "totalCost": 1}
        session, mock = mock_http(
            response(200, {"data": [], "meta": {"page": 1, "totalPages": 2}}),
            response(200, {"data": [trace], "meta": {"page": 2, "totalPages": 2}}),
        )
        with mock:
            batch = normalize_rows(fetch_live(read_config(ENV)))
        self.assertEqual(len(batch.traces), 1)
        self.assertEqual(batch.traces[0].prompt, "question")
        self.assertEqual(batch.traces[0].response, "answer")
        self.assertEqual(batch.traces[0].account_id, "account")
        self.assertEqual(batch.traces[0].session_id, "session")
        self.assertEqual(batch.traces[0].turn_number, 2)
        self.assertEqual(batch.traces[0].timestamp.isoformat(), "2025-01-01T00:00:00+00:00")
        self.assertEqual(batch.traces[0].cost_usd, 1)
        self.assertEqual(batch.traces[0].latency_seconds, 3.5)
        first, second = session.get.call_args_list
        self.assertEqual(first.kwargs["params"], {
            "fromTimestamp": "2024-12-31T22:00:00+00:00",
            "toTimestamp": "2025-01-03T00:00:00+00:00",
            "limit": 50, "page": 1,
        })
        self.assertEqual(second.kwargs["params"]["page"], 2)
        self.assertEqual(first.args[0], "https://example.invalid/api/public/traces")
        self.assertFalse(first.kwargs["allow_redirects"])
        self.assertFalse(session.trust_env)

    def test_live_retries_and_refuses_repeated_page_numbers(self):
        page = {"data": [], "meta": {"page": 1, "totalPages": 2}}
        session, mock = mock_http(response(429, headers={"Retry-After": "2"}), response(200, page), response(200, page))
        with mock, patch("src.fetcher.sleep") as sleep:
            with self.assertRaisesRegex(LiveError, "invalid pagination"):
                fetch_live(read_config(ENV))
        sleep.assert_called_once_with(2)
        self.assertEqual(session.get.call_count, 3)

    def test_live_accepts_empty_window_and_reports_unavailable_endpoint(self):
        session, mock = mock_http(response(200, {"data": [], "meta": {"page": 1, "totalPages": 0}}))
        with mock:
            self.assertEqual(fetch_live(read_config(ENV)), ())
        session.get.assert_called_once()
        session, mock = mock_http(response(410))
        with mock:
            with self.assertRaisesRegex(LiveError, "traces endpoint unavailable"):
                fetch_live(read_config(ENV))
        session.get.assert_called_once()

    def test_cli_failure_writes_sanitized_stderr_only(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["--unknown-secret"])
        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("unknown-secret", stderr.getvalue())
