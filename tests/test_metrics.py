import unittest

from src.analyzer import analyze
from src.fetcher import normalize_rows


def trace(number, *, account="a", session="s", turn=1, time=None, prompt="", reply="", status="completed", cost=None):
    return {
        "id": str(number), "sessionId": session, "timestamp": time, "totalCost": cost, "latency": 2,
        "metadata": {"account_uuid": account, "turn_number": turn, "status": status},
        "input": {"user_prompt": prompt}, "output": {"response": reply},
    }


class MetricsTests(unittest.TestCase):
    def test_single_observation_and_first_turn_are_separate(self):
        result = analyze(normalize_rows([
            trace(1, turn=1),
            trace(2, session="t", turn=3),
            trace(3, session="u", turn=1, account="b"),
            trace(4, session="u", turn=2, account="b"),
        ]), "local_json", include_review=True)
        metrics = result["user_dynamics"]
        self.assertEqual(metrics["single_observed_turn"], {"count": 2, "eligible_count": 3, "ratio": 0.666667})
        self.assertEqual(metrics["single_observed_at_turn_1"]["count"], 1)
        self.assertEqual(metrics["sessions_per_user"]["p90"], 2)
        self.assertEqual(result["friction_metrics"]["last_observed_turn_counts"], {"1": 1, "2": 1, "3": 1})
        self.assertEqual(result["user_summaries"][0]["single_observed_turn"]["count"], 0)

    def test_timestamps_use_utc_and_skip_overlapping_gaps(self):
        result = analyze(normalize_rows([
            trace(1, time="2025-01-01T00:00:00Z"),
            trace(2, turn=2, time="2025-01-01T05:00:00Z"),
            trace(3, session="nested", time="2025-01-01T01:00:00Z"),
            trace(4, session="later", time="2025-01-01T08:00:00+02:00"),
            trace(5, account="b", session="other"),
        ]), "local_json")
        clock = result["time_distribution"]
        self.assertEqual(clock["between_00_and_06"], {"count": 3, "eligible_count": 4, "ratio": 0.75})
        self.assertEqual(clock["between_01_and_04"], {"count": 1, "eligible_count": 4, "ratio": 0.25})
        self.assertEqual(clock["missing_timestamp_count"], 1)
        self.assertEqual(result["user_dynamics"]["inter_session_hours"]["mean"], 1)

    def test_signals_are_counted_from_correct_side_with_denominators(self):
        result = analyze(normalize_rows([
            trace(1, prompt="מה אומר BMI 22? זה מדאיג אותי", reply="כדאי לפנות לרופא המטפל."),
            trace(2, turn=2, prompt="דחוף מה המשמעות של בדיקת דם", reply="100 mg/dL"),
            trace(3, turn=3, prompt="עברו 2 ביקורים", status="error"),
            trace(4, turn=4, status="error"),
        ]), "local_json", include_review=True)
        signals = result["clinical_signals"]
        for key in ("numeric_measurement_mention", "concern_wording", "urgency_wording"):
            self.assertEqual(signals[key], {"count": 1, "eligible_count": 3, "ratio": 0.333333})
        friction = result["friction_metrics"]
        self.assertEqual(friction["doctor_referral_wording"], {"count": 1, "eligible_count": 2, "ratio": 0.5})
        self.assertEqual((friction["error_trace_count"], friction["users_with_errors"]), (2, 1))
        self.assertEqual(result["user_summaries"][0]["longest_observed_error_streak"], 2)

    def test_cost_coverage_and_reformulation(self):
        result = analyze(normalize_rows([
            trace(1, prompt="מה משפיע על שינוי רוחב העורקים?", cost=3),
            trace(2, turn=2, prompt="מה משפיע על שינוי ברוחב העורקים בעין?", cost=1),
            trace(3, account="b", prompt="מה אומר BMI?", cost=1),
            trace(4, account="c", prompt="מה אומר BMI?"),
        ]), "local_json")
        costs = result["cost_and_latency"]
        self.assertEqual(costs["top_10_percent_user_cost"], {
            "eligible_user_count": 2, "top_user_count": 1, "cost_share": 0.8,
        })
        self.assertEqual(costs["by_topic"]["body_composition"]["trace_cost_usd"], {"count": 1, "mean": "1.000000"})
        self.assertEqual(result["friction_metrics"]["similar_reformulation_count"], 1)
        self.assertEqual(result["friction_metrics"]["exact_repeat_count"], 0)

    def test_empty_dataset_uses_null_ratios_and_means(self):
        result = analyze(normalize_rows([]), "langfuse_traces")
        self.assertIsNone(result["user_dynamics"]["single_observed_turn"]["ratio"])
        self.assertIsNone(result["time_distribution"]["between_00_and_06"]["ratio"])
        self.assertIsNone(result["cost_and_latency"]["top_10_percent_user_cost"]["cost_share"])
