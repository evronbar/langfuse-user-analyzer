import json
import unittest

from src.analyzer import analyze
from src.fetcher import normalize_rows


def row(number, prompt, reply="", *, account="synthetic-user", session="synthetic-session", status="completed"):
    return {
        "id": f"synthetic-{account}-{session}-{number}",
        "sessionId": session,
        "metadata": {"account_uuid": account, "turn_number": number, "status": status},
        "input": {"user_prompt": prompt},
        "output": {"response": reply},
    }


def signals(result, index=0):
    return {signal["kind"]: signal for signal in result["review"][index]["conversation_signals"]}


class ConversationSignalsTests(unittest.TestCase):
    def review(self, *rows):
        return analyze(normalize_rows(rows), "local_json", include_review=True)

    def test_first_appearance_is_different_from_reuse_within_session(self):
        result = self.review(
            row(1, "איך הבדיקות שלי?", "רמת HbA1c היא 6.53%."),
            row(2, "אפשר להסביר?", "רמת HbA1c היא 6.53%."),
            row(1, "איך הבדיקות שלי?", "רמת HbA1c היא 6.53%.", session="another"),
        )
        self.assertEqual(signals(result)["agent_introduced_measurement"]["observation"], "new_metric_and_value")
        self.assertNotIn("agent_introduced_measurement", signals(result, 1))
        self.assertIn("agent_introduced_measurement", signals(result, 2))
        self.assertEqual(result["conversation_signals"]["agent_introduced_measurement"]["known_user_count"], 1)
        self.assertNotIn("6.53", json.dumps(result))

    def test_unitless_number_distinguishes_clarification_and_agent_assumption(self):
        for reply, expected in (
            ("מה היחידות ובאיזה מדד מדובר?", "asked_for_clarification"),
            ("זה גלוקוז.", "agent_named_metric_without_user_context"),
        ):
            with self.subTest(reply=reply):
                result = self.review(row(1, "יצא לי 110", reply))
                self.assertEqual(signals(result)["ambiguous_numeric_input"]["observation"], expected)
        result = self.review(row(1, "אני שואל על גלוקוז"), row(2, "יצא לי 110", "זה גלוקוז."))
        self.assertEqual(signals(result, 1)["ambiguous_numeric_input"]["observation"], "prior_user_metric_available")

    def test_temporal_question_requires_time_and_numbers_to_show_comparison_material(self):
        result = self.review(
            row(1, "זה עלה או ירד?", "כל ערך משתנה."),
            row(2, "זה עלה או ירד?", "בבדיקה הקודמת 100 mg/dL ובאחרונה 110 mg/dL."),
        )
        self.assertEqual(signals(result)["temporal_question"]["observation"], "no_temporal_comparison_detected")
        self.assertEqual(signals(result, 1)["temporal_question"]["observation"], "time_and_new_values_in_reply")

    def test_treatment_concern_and_cross_domain_are_separate(self):
        result = self.review(
            row(1, "זה מסוכן? כמה כדורים לקחת?", "אינני רופא. כדאי לפנות לרופא."),
            row(2, "האם חוסר שינה העלה לי את הסוכר?"),
            row(3, "מה בודקים בצילום רשתית?"),
        )
        self.assertEqual(signals(result)["concern_or_urgency_wording"]["observation"], "reply_mentions_doctor")
        self.assertEqual(signals(result)["medication_or_supplement_request"]["observation"], "reply_states_boundary_or_referral")
        self.assertEqual(signals(result, 1)["cross_domain_request"]["observation"], "relationship_question")
        self.assertEqual(signals(result, 1)["cross_domain_request"]["categories"], ("blood_tests", "sleep"))
        self.assertIn("retina_explanation_request", signals(result, 2))

    def test_explicit_pushback_and_brevity_require_adjacent_user_turn(self):
        result = self.review(
            row(1, "מה זה אומר?", "אינני רופא. " + "מילה " * 220),
            row(2, "לא ענית לי, בקצרה בבקשה"),
        )
        self.assertEqual(signals(result)["disclaimer_follow_up"]["observation"], "explicit_pushback")
        self.assertEqual(signals(result)["long_reply_to_short_prompt"]["observation"], "user_asked_for_brevity")
        gap = self.review(row(1, "מה זה אומר?", "אינני רופא."), row(3, "לא ענית לי"))
        self.assertNotIn("disclaimer_follow_up", signals(gap))

    def test_jargon_requires_term_in_previous_agent_text(self):
        result = self.review(row(1, "מה אומר המדד?", "המדד נקרא TEM."), row(2, "מה זה TEM?"))
        self.assertEqual(signals(result, 1)["jargon_follow_up"]["related_position"], 1)
        self.assertNotIn("jargon_follow_up", signals(self.review(row(1, "מה זה TEM?"))))

    def test_nonempty_error_is_only_an_observation(self):
        result = self.review(row(1, "אפשר הסבר?", "חלק מהטקסט נשמר", status="error"))
        self.assertEqual(signals(result)["nonempty_reply_with_error"]["observation"], "error_recorded_with_reply_text")
