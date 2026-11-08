import json
import unittest

from src.analyzer import analyze
from src.fetcher import normalize_rows


def turn(number, prompt, reply="", *, session="synthetic-session"):
    return {
        "id": f"synthetic-{session}-{number}", "sessionId": session,
        "metadata": {"account_uuid": "synthetic-account", "turn_number": number},
        "input": {"user_prompt": prompt}, "output": {"response": reply},
    }


def review(*rows):
    return analyze(normalize_rows(rows), "local_json", include_review=True)


class UnderstandingTests(unittest.TestCase):
    def test_panel_and_literature_are_separate_intents_with_same_anchor(self):
        result = review(
            turn(1, "איפה נמצא פאנל Nightingale?"),
            turn(2, "שלוף את כל הפאנל ותסביר"),
            turn(3, "הסבר נתון נתון מה אומר והאם תקין ביחס לספרות"),
        )
        request = result["review"][2]["understanding_request"]
        self.assertEqual(set(request["understanding_intent"]), {"panel_breakdown", "literature_norm_comparison"})
        self.assertEqual(request["test_target"], {"domain": "blood_tests", "specific_metric": "nightingale_panel"})
        self.assertEqual((request["resolution_source"], request["target_position"]), ("session_context", 1))

    def test_temporal_request_and_agent_only_metric_are_separate(self):
        result = review(turn(1, "מה המשמעות של העליה בין הבדיקות?", "מדובר בצפיפות העצם, הערך הוא 731.22."))
        request = result["review"][0]["understanding_request"]
        self.assertEqual(request["understanding_intent"], ("temporal_trend",))
        self.assertEqual(request["test_target"], {"domain": "bone_density", "specific_metric": "bone_density"})
        self.assertEqual(request["resolution_source"], "agent_inferred")
        self.assertEqual(result["review"][0]["topics"], ())
        self.assertNotIn("731.22", json.dumps(result))

    def test_numeric_question_stays_unresolved_without_measurement(self):
        request = review(turn(1, "האם 110 זה תקין?"))["review"][0]["understanding_request"]
        self.assertEqual(request["understanding_intent"], ("interpret_single_value",))
        self.assertEqual(request["test_target"], {"domain": None, "specific_metric": None})
        self.assertEqual(request["resolution_source"], "unresolved")

    def test_direct_target_wins_over_agent_guess(self):
        request = review(turn(1, "מה אומר HbA1c?", "המדד הוא כולסטרול."))["review"][0]["understanding_request"]
        self.assertEqual(request["test_target"]["specific_metric"], "hba1c")
        self.assertEqual(request["resolution_source"], "user_direct")

    def test_no_cross_session_inheritance_or_arbitrary_choice_among_metrics(self):
        result = review(
            turn(1, "מה אומר כולסטרול?"),
            turn(1, "האם זה תקין?", session="another"),
            turn(2, "מה אומר כולסטרול ומה אומר HbA1c?"),
        )
        unresolved = next(row for row in result["review"] if row["session_number"] == 2)
        self.assertEqual(unresolved["understanding_request"]["resolution_source"], "unresolved")
        multiple = result["review"][1]["understanding_request"]["test_target"]
        self.assertEqual(multiple, {"domain": "blood_tests", "specific_metric": None})

    def test_report_and_medication_request_do_not_become_test_interpretation(self):
        result = review(
            turn(1, "שמתי לב לשינוי בכולסטרול."),
            turn(2, "מה אפשר לעשות כדי לשפר את הכולסטרול?"),
        )
        self.assertTrue(all(row["understanding_request"] is None for row in result["review"]))

    def test_cohort_and_change_are_counted_independently(self):
        result = review(turn(1, "מבחינת השינוי בשומן הויסצרלי, איפה אני ממוקם ביחס למשתתפים, כמה זה נורמלי?"))
        self.assertEqual(result["understanding_requests"]["by_intent"], {"cohort_comparison": 1, "temporal_trend": 1})
        self.assertEqual(result["understanding_requests"]["trace_count"], 1)

    def test_concept_explanation_is_not_a_temporal_question(self):
        request = review(turn(1, "מה בכלל בודקים בצילום רשתית?"))["review"][0]["understanding_request"]
        self.assertEqual(request["understanding_intent"], ("concept_explanation",))
        self.assertEqual(request["test_target"], {"domain": "retina_imaging", "specific_metric": None})
