import json
import unittest

from src.analyzer import analyze
from src.fetcher import normalize_rows


def turn(number, prompt, *, session="synthetic-session", account="synthetic-account", response="", turn_number=None):
    return {
        "id": f"synthetic-{number}",
        "sessionId": session,
        "metadata": {"account_uuid": account, "turn_number": turn_number or number},
        "input": {"user_prompt": prompt},
        "output": {"response": response},
    }


class ContextAndStateTests(unittest.TestCase):
    def analyze(self, *rows, include_review=True):
        return analyze(normalize_rows(rows), "local_json", include_review=include_review)

    def test_consecutive_references_keep_original_anchor(self):
        result = self.analyze(
            turn(1, "אפשר לראות את התוצאות של בדיקת דם?"),
            turn(2, "ומה זה אומר?"),
            turn(3, "האם זה תקין?"),
        )
        reviews = result["review"]
        self.assertEqual([row["topics"] for row in reviews], [("blood_tests",)] * 3)
        self.assertEqual([row["topic_anchor_position"] for row in reviews], [1, 1, 1])
        self.assertEqual([row["topic_source"] for row in reviews], ["direct", "previous_user_turn", "previous_user_turn"])
        topic = next(t for t in result["topics"] if t["topic"] == "blood_tests")
        self.assertEqual((topic["trace_count"], topic["contextual_trace_count"]), (3, 2))

    def test_new_direct_topic_takes_precedence_and_new_term_breaks_chain(self):
        result = self.analyze(
            turn(1, "אני רוצה להבין את בדיקת דם."),
            turn(2, "האם המדדים האלה קשורים לאיכות השינה?"),
            turn(3, "מה זה XYZ?"),
            turn(4, "ומה זה אומר?"),
        )
        reviews = result["review"]
        self.assertEqual((reviews[1]["topics"], reviews[1]["topic_source"]), (("sleep",), "direct"))
        self.assertEqual([row["topics"] for row in reviews[2:]], [(), ()])

    def test_missing_turn_ambiguous_anchor_and_agent_only_context_abstain(self):
        cases = (
            (
                turn(1, "אפשר לדבר על בדיקת דם?"),
                turn(2, "ומה זה אומר?", turn_number=4),
            ),
            (
                turn(1, "אפשר להשוות בין בדיקת דם ובין מדדי שינה?"),
                turn(2, "האם זה תקין?"),
            ),
            (
                turn(1, "היי", response="אני יכול לעזור עם בדיקת דם."),
                turn(2, "האם זה תקין?"),
            ),
            (
                turn(1, "מה אומרת בדיקת דם?"),
                turn(2, "ומה זה אומר?", session="other-session"),
            ),
        )
        for rows in cases:
            with self.subTest(rows=rows):
                reviews = self.analyze(*rows)["review"]
                self.assertEqual(reviews[1]["topic_source"], "unknown")
                self.assertIsNone(reviews[1]["topic_anchor_position"])

    def test_user_summary_keeps_reports_concerns_questions_and_goals_separate(self):
        result = self.analyze(
            turn(1, "אני מרגישה עייפה אחרי שינה. האם זה תקין?"),
            turn(2, "זה מדאיג אותי"),
            turn(3, "איך לשפר את השינה?", session="other-session", turn_number=1),
            turn(4, "האם יש לי דום נשימה?", account="second-account"),
            turn(5, "אני מרגיש עייף אחרי שינה.", account=None),
        )
        summary = result["user_summaries"][0]
        signals = {row["signal"]: row for row in summary["state"]}
        self.assertEqual(summary["session_count"], 2)
        self.assertEqual(set(signals), {"self_report", "measurement_question", "expressed_concern", "action_goal"})
        self.assertEqual(signals["self_report"]["topic"], "sleep")
        self.assertEqual(signals["expressed_concern"]["topic"], "sleep")
        self.assertEqual(signals["expressed_concern"]["evidence"][0]["topic_anchor_position"], 1)
        self.assertEqual(signals["action_goal"]["evidence"][0]["session_number"], 2)
        self.assertEqual(result["state_users"]["self_report"], 1)
        self.assertEqual(result["state_users"]["clinical_concern"], 1)
        self.assertNotIn("synthetic-account", json.dumps(result))

    def test_default_output_keeps_user_level_details_private(self):
        result = self.analyze(turn(1, "שמתי לב לשינוי בשינה."), include_review=False)
        self.assertEqual(result["state_users"], {"self_report": 1})
        self.assertNotIn("user_summaries", result)
        self.assertNotIn("review", result)

    def test_definite_visceral_fat_is_a_direct_topic_in_state_evidence(self):
        result = self.analyze(turn(1, "מה המשמעות של השינוי בשומן הוויסצרלי?"))
        summary = result["user_summaries"][0]["state"][0]
        self.assertEqual((summary["signal"], summary["topic"]), ("measurement_question", "body_composition"))
        self.assertEqual(summary["evidence"][0]["topic_source"], "direct")
