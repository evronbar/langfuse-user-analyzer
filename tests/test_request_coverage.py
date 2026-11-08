import json
import unittest

from src.analyzer import analyze
from src.fetcher import normalize_rows


def evaluate(prompt, response, status="completed"):
    batch = normalize_rows([{
        "id": "synthetic-id", "sessionId": "synthetic-session",
        "input": {"user_prompt": prompt}, "output": {"response": response},
        "metadata": {"status": status, "account_uuid": "synthetic-account", "turn_number": 1},
    }])
    return analyze(batch, "local_json", include_review=True)


def components(result):
    return {part["kind"]: part["status"] for part in result["review"][0]["request_components"]}


class RequestCoverageTests(unittest.TestCase):
    def test_panel_without_explanation_marks_only_the_missing_component(self):
        result = evaluate("שלוף את כל הפאנל ותסביר", "מדד א: 80\nמדד ב: 30")
        self.assertEqual(components(result), {
            "retrieve_panel": "addressed", "explain_values": "omitted_without_explanation",
        })
        self.assertEqual(result["request_coverage"]["component_count"], 2)
        self.assertEqual(result["request_coverage"]["turns_with_components"], 1)

    def test_scoped_explanation_boundary_does_not_hide_supplied_panel(self):
        result = evaluate(
            "תציג את הפאנל ותסביר",
            "מדד א: 80\nמדד ב: 30\nאינני יכול להסביר מה משמעות הערכים.",
        )
        self.assertEqual(components(result), {
            "retrieve_panel": "addressed", "explain_values": "stated_boundary",
        })

    def test_missing_cohort_is_a_reported_blocker(self):
        result = evaluate(
            "איפה אני ממוקם ביחס למשתתפים במחקר?",
            "האחוזון אינו נגיש. הנה השינוי בין ביקור 1 לביקור 2, 4 גרם.",
        )
        self.assertEqual(components(result), {"compare_cohort": "reported_blocker"})

    def test_owner_answer_distinguishes_identity_from_connected_account(self):
        prompt = "זה של דנה או אורי?"
        incomplete = evaluate(prompt, "הנתונים שייכים לחשבון שאליו התחברת.")
        self.assertEqual(components(incomplete), {"identify_owner": "omitted_without_explanation"})
        self.assertEqual(components(evaluate(prompt, "הנתונים של דנה.")), {"identify_owner": "addressed"})
        self.assertNotIn("דנה", json.dumps(incomplete, ensure_ascii=False))

    def test_empty_error_is_separate_from_missing_evidence(self):
        prompt = "שלוף את כל הפאנל ותסביר"
        for status, expected in (("error", "execution_error"), ("completed", "unknown")):
            with self.subTest(status=status):
                self.assertEqual(components(evaluate(prompt, "", status)), {
                    "retrieve_panel": expected, "explain_values": expected,
                })

    def test_promise_does_not_count_as_actual_data_or_explanation(self):
        result = evaluate("שלוף את כל הפאנל ותסביר", "אוכל להציג את הפאנל בהמשך.")
        self.assertEqual(components(result), {"retrieve_panel": "unknown", "explain_values": "unknown"})

    def test_panel_location_accepts_explicit_chat_access_but_not_unavailability(self):
        prompt = "איפה אני רואה את הפאנל המלא?"
        self.assertEqual(components(evaluate(prompt, "הפאנל המלא זמין לך כאן, בשיחה הזו.")),
                         {"locate_panel": "addressed"})
        self.assertEqual(components(evaluate(prompt, "הפאנל המלא לא זמין כאן.")),
                         {"locate_panel": "reported_blocker"})

    def test_unscoped_boundary_does_not_override_actual_explanation(self):
        prompt = "הסבר נתון נתון מה אומר"
        self.assertEqual(components(evaluate(prompt, "אינני יכול לאבחן. הערך מייצג ריכוז בדם.")),
                         {"explain_values": "addressed"})
        self.assertEqual(components(evaluate(prompt, "אינני יכול להסביר מה הערך מייצג.")),
                         {"explain_values": "stated_boundary"})

    def test_clinical_evidence_is_distinct_from_diagnosis_boundary(self):
        prompt = "האם יש עדות לדום נשימה?"
        for answer in (
            "אין עדות לדום נשימה במדידות שנבדקו.",
            "התסמינים עשויים להתאים לדום נשימה.",
            "המדד אינו מעיד בהכרח על דום נשימה.",
        ):
            with self.subTest(answer=answer):
                result = evaluate(prompt, f"אינני רופא ואינני יכול לתת אבחנה\n\n{answer}")
                self.assertEqual(components(result), {"clinical_assessment": "addressed"})
        self.assertEqual(components(evaluate(prompt, "אינני יכול לתת אבחנה.")),
                         {"clinical_assessment": "stated_boundary"})

    def test_natural_explanations_are_recognized_independently(self):
        prompt = "שלוף את כל הפאנל ותסביר"
        for answer in ("התוצאה היא בטווח תקין.", "הערך מעיד על ריכוז גבוה.",
                       "הנתון מצביע על כך שהערך ירד.", "הבדיקה מראה שהערך עלה."):
            with self.subTest(answer=answer):
                result = evaluate(prompt, f"אינני יכול לשלוף את הפאנל.\n\n{answer}")
                self.assertEqual(components(result), {"retrieve_panel": "stated_boundary",
                                                      "explain_values": "addressed"})
