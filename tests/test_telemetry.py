import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import find_spec
from unittest.mock import patch

from src.main import main


class TelemetryTests(unittest.TestCase):
    def test_trace_requires_explicit_export_credentials(self):
        stderr = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), redirect_stderr(stderr):
            self.assertEqual(main(["--trace", "--input-json", "test.json"]), 2)
        self.assertIn("missing LANGFUSE_BASE_URL", stderr.getvalue())

    @unittest.skipUnless(find_spec("langfuse"), "Langfuse SDK required")
    def test_cli_exports_only_operational_spans_even_when_a_stage_fails(self):
        from langfuse import Langfuse
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        class Encoder:
            def __init__(self, directory):
                pass

            def encode(self, texts):
                raise RuntimeError("private-error-message")

        exporter = InMemorySpanExporter()
        env = {
            "LANGFUSE_BASE_URL": "https://example.invalid",
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test-analyzer-export",
            "LANGFUSE_SECRET_KEY": "sk-lf-private-secret",
        }
        rows = [{
            "id": "private-trace-id",
            "input": {"user_prompt": "private-prompt-text"},
            "output": {"response": "private-reply-text"},
        }]
        with patch.dict("os.environ", env), patch(
            "langfuse.Langfuse", side_effect=lambda **kwargs: Langfuse(span_exporter=exporter, **kwargs),
        ), patch("src.main.read_local", return_value=rows), patch(
            "src.main.LocalE5", Encoder,
        ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = main(["--trace", "--input-json", "private-source-path", "--semantic-model", "private-model-path"])

        self.assertEqual(result, 6)
        spans = {span.name: span for span in exporter.get_finished_spans()}
        self.assertEqual(set(spans), {
            "user-analyzer.run", "load-local-e5", "read-local-traces",
            "normalize-traces", "analyze-conversations", "embed-local-e5",
        })
        root = spans["user-analyzer.run"]
        analysis = spans["analyze-conversations"]
        embedding = spans["embed-local-e5"]
        self.assertIsNone(root.parent)
        self.assertEqual(analysis.parent.span_id, root.context.span_id)
        self.assertEqual(embedding.parent.span_id, analysis.context.span_id)
        self.assertEqual(embedding.attributes["langfuse.observation.type"], "embedding")
        for span in (root, analysis, embedding):
            self.assertEqual(span.attributes["langfuse.observation.level"], "ERROR")
            self.assertFalse(span.events)
        serialized = "\n".join(span.to_json() for span in spans.values())
        self.assertNotIn("private-", serialized)
