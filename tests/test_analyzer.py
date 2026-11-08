import json
import unittest

from src.analyzer import analyze
from src.fetcher import normalize_rows


def row(number, prompt, response, *, turn=None, session="private-session", account="private-account", status="completed"):
    return {
        "id": f"private-trace-{number}",
        "sessionId": session,
        "metadata": {"account_uuid": account, "turn_number": turn or number, "status": status},
        "input": {"user_prompt": prompt},
        "output": {"response": response},
    }


class AnalyzerTests(unittest.TestCase):
    def review(self, *rows):
        return analyze(normalize_rows(rows), "local_json", include_review=True)

    def test_repeat_and_partial_answer_do_not_imply_dissatisfaction(self):
        prompt = "איפה אני ביחס למשתתפים אחרים?"
        result = self.review(
            row(2, prompt, "הנתון אינו נגיש. השינוי בין ביקור 1 לביקור 2 הוא 4 גרם."),
            row(1, prompt, "לא הצלחתי לשלוף את ההשוואה."),
        )
        first, second = result["review"]
        self.assertEqual(first["intent"], "compare_or_benchmark")
        self.assertEqual((first["delivery"], first["next_user_action"]), ("open", "repeated"))
        self.assertEqual(second["delivery"], "partial")
        self.assertEqual(result["conversation"]["satisfaction"], {"unknown": 2})
        repeat = result["friction_metrics"]["repeat_after_incomplete"]
        self.assertEqual((repeat["count"], repeat["eligible_count"]), (1, 1))
        self.assertNotIn("private-", json.dumps(result))
        self.assertNotIn(prompt, json.dumps(result, ensure_ascii=False))

    def test_refinement_after_error_and_new_request_after_explanation(self):
        result = self.review(
            row(1, "שלוף את הפאנל", "", status="error"),
            row(2, "הסבר נתון נתון מה אומר", "המדד מתאר ערך יחסי."),
            row(3, "מה אפשר לעשות כדי לשפר?", "כדאי לברר את זה עם רופא."),
        )
        first, second, _ = result["review"]
        self.assertEqual((first["execution_status"], first["delivery"]), ("error", "open"))
        self.assertEqual(first["next_user_action"], "refined")
        self.assertEqual(second["next_user_action"], "new_request")

    def test_explicit_feedback_scope_and_no_attribution_across_sessions(self):
        result = self.review(
            row(1, "אפשר לקבל סיסמה?", "לא אפשרי."),
            row(2, "מעולה, בדקתי אותך", "תודה."),
            row(3, "זה לא עזר", "", session="other-session"),
        )
        first, second, third = result["review"]
        self.assertEqual((first["satisfaction"], first["satisfaction_scope"]), ("positive", "interaction"))
        self.assertEqual(second["next_user_action"], "none_observed")
        self.assertEqual(third["satisfaction"], "unknown")
        self.assertEqual(result["conversation"]["satisfaction"], {"positive": 1, "unknown": 2})

    def test_thanks_missing_turns_and_unknown_account_leave_feedback_unknown(self):
        result = self.review(
            row(1, "מה אומר המדד?", "הסבר"),
            row(2, "תודה", "בבקשה"),
            row(3, "זה עזר", "", turn=4),
            row(4, "מה זה X?", "הסבר", account=None),
            row(5, "זה לא עזר", "", account=None),
        )
        first, second, *_ = result["review"]
        self.assertEqual(first["satisfaction"], "unknown")
        self.assertIn("nonconsecutive_recorded_turns", second["evidence"])
        self.assertEqual(result["conversation"]["satisfaction"], {"unknown": 5})

    def test_reach_and_state_use_user_text(self):
        result = self.review(
            row(1, "האם יש עדות לדום נשימה?", "נמדדו ערכים."),
            row(2, "מה זה XYZ?", "האם זה קשור לשינה?"),
            row(3, "מה זה ABC?", "אין לי מספיק מידע.", session="other-session", account="other-account"),
        )
        self.assertIn("clinical_concern", result["review"][0]["state_signals"])
        self.assertFalse(result["review"][1]["state_signals"])
        sleep = next(topic for topic in result["topics"] if topic["topic"] == "sleep")
        self.assertEqual(sleep["trace_count"], 1)
        terms = next(intent for intent in result["intents_by_reach"] if intent["intent"] == "define_term")
        self.assertEqual((terms["trace_count"], terms["session_count"], terms["user_count"]), (2, 2, 2))

    def test_topic_population_excludes_social_turns_but_keeps_mixed_requests(self):
        result = self.review(
            row(1, "שלום!", "שלום."),
            row(2, "תודה רבה", "בבקשה."),
            row(3, "אפשר לקבל סיסמה?", "לא אפשרי."),
            row(4, "תודה, איך דופק ולחץ דם קשורים לתזונה וויטמינים?", "הסבר."),
            row(5, "יש לי כאבים ואני מודאגת מהבריאות שלי", "הסבר."),
            row(6, "מה זה QZX?", "הסבר."),
        )
        self.assertEqual(result["topic_population"], {
            "eligible_trace_count": 3,
            "excluded_trace_count": 3,
            "excluded_by_reason": {"access_and_identity": 1, "chitchat": 1, "feedback": 1},
            "unclassified_trace_count": 1,
            "semantic_trace_count": 0,
        })
        self.assertEqual(result["review"][3]["topics"], ("nutrition", "cardiovascular"))
        self.assertEqual(result["review"][4]["topics"], ("pain",))
