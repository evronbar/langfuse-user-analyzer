import io
import re
import unittest
from base64 import b64decode
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.analyzer import analyze
from src.fetcher import normalize_rows
from src.main import main
from src.models import ReportError
from src.reporter import attention_summary, format_rate, print_pdf, render_report, write_pdf


class ReporterTests(unittest.TestCase):
    def test_rate_uses_counts_and_preserves_zero_denominator(self):
        self.assertEqual(format_rate({"count": 1, "eligible_count": 4}), ("1 / 4", "25.0%"))
        self.assertEqual(format_rate({"count": 0, "eligible_count": 4}), ("0 / 4", "0.0%"))
        self.assertEqual(format_rate({"count": 0, "eligible_count": 0}), ("-", "-"))

    def test_pdf_rejects_invalid_analysis_and_file_extension(self):
        with self.assertRaisesRegex(ReportError, "aggregate analysis"):
            write_pdf({}, Path("ignored.pdf"))
        with self.assertRaisesRegex(ReportError, "end in .pdf"):
            write_pdf({}, Path("ignored.json"))

    def test_main_handles_pdf_failure_before_printing_results(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("src.main.read_local", return_value=()), patch(
            "src.main.write_pdf", side_effect=ReportError("could not write PDF")
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--input-json", "sample.json", "--output", "report.pdf"])
        self.assertEqual(code, 5)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("report: could not write PDF", stderr.getvalue())

    def test_pdf_renders_empty_analysis_and_preserves_previous_file_on_failure(self):
        result = analyze(normalize_rows([]), "local_json")
        def render(browser, source, output):
            self.assertIn('dir="rtl"', source.read_text(encoding="utf-8"))
            output.write_bytes(b"%PDF-1.7")

        with TemporaryDirectory() as folder, patch("src.reporter.find_browser", return_value=Path("browser")), patch(
            "src.reporter.print_pdf", side_effect=render
        ):
            path = Path(folder) / "report.pdf"
            write_pdf(result, path)
            previous = path.read_bytes()
            self.assertTrue(previous.startswith(b"%PDF-"))
            with patch("src.reporter.render_report", side_effect=OSError("test")):
                with self.assertRaises(ReportError):
                    write_pdf(result, path)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_nonempty_report_embeds_real_chart_and_uses_only_aggregates(self):
        result = analyze(normalize_rows([{
            "id": "private-trace", "input": {"user_prompt": "איך השינה? private-prompt"},
        }]), "local_json")
        html = render_report(result)
        chart = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html)
        self.assertIsNotNone(chart)
        self.assertTrue(b64decode(chart[1]).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("חיכוך ותסכול", html)
        self.assertIn("שינה 1", html)
        self.assertNotIn("private-", html)

    def test_layout_keeps_singular_counts_and_mixed_direction_rate_together(self):
        result = analyze(normalize_rows([]), "local_json")
        result["summary"]["user_count"] = 1
        result["state_finding_users_by_focus"] = {"interpret_measurement": 1}
        result["friction_metrics"]["doctor_referral_wording"] = {"count": 7, "eligible_count": 24}
        result["friction_metrics"]["repeat_after_incomplete"] = {"count": 1, "eligible_count": 4}
        result["request_coverage"] = {"component_count": 1, "by_status": {"addressed": 1}}
        html = render_report(result)
        self.assertIn('dir="rtl">משתמש 1</b>', html)
        self.assertIn('dir="rtl">רכיב 1</b>', html)
        self.assertIn("במקרה 1 זוהתה", html)
        self.assertIn('<bdi dir="ltr">7/24</bdi> מהתשובות הלא ריקות', html)

    def test_attention_distinguishes_zero_from_no_eligible_messages(self):
        key = "concern_or_urgency_wording"
        self.assertEqual(attention_summary({key: {"count": 0, "eligible_count": 26}})[0], "0%")
        self.assertEqual(attention_summary({key: {"count": 0, "eligible_count": 0}})[0], "—")

    def test_pdf_falls_back_to_system_temp_on_directory_permission_error(self):
        result = analyze(normalize_rows([]), "local_json")
        def temporary_directory(**kwargs):
            if "dir" in kwargs:
                raise PermissionError
            return TemporaryDirectory(**kwargs)

        def render(browser, source, output):
            self.assertNotEqual(source.parent.parent, path.parent)
            output.write_bytes(b"%PDF-1.7")

        with TemporaryDirectory() as folder, patch("src.reporter.find_browser", return_value=Path("browser")), patch(
            "src.reporter.TemporaryDirectory", side_effect=temporary_directory
        ), patch("src.reporter.print_pdf", side_effect=render):
            path = Path(folder) / "report.pdf"
            write_pdf(result, path)
            self.assertEqual(path.read_bytes(), b"%PDF-1.7")

    def test_container_flag_enables_chromium_options_without_dockerenv(self):
        with patch.dict("os.environ", {"DOCKER_CONTAINER": "1"}), patch("src.reporter.subprocess.run") as run:
            print_pdf(Path("browser"), Path("report.html").resolve(), Path("report.pdf"))
        self.assertIn("--no-sandbox", run.call_args.args[0])
        self.assertIn("--disable-setuid-sandbox", run.call_args.args[0])
        self.assertIn("--disable-dev-shm-usage", run.call_args.args[0])

    def test_main_uses_default_output_for_container_analysis_only(self):
        with patch.dict("os.environ", {"DOCKER_CONTAINER": "1"}), patch(
            "src.main.read_local", return_value=()
        ), patch("src.main.write_pdf") as write, redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--input-json", "sample.json"]), 0)
            self.assertEqual(write.call_args.args[1], Path("/output/report.pdf"))
            write.reset_mock()
            self.assertEqual(main(["--input-json", "sample.json", "--summary-only"]), 0)
            write.assert_not_called()
