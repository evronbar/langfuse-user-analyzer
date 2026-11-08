import json
import unittest

from src.analyzer import analyze
from src.fetcher import normalize_rows


def turn(number, prompt, reply="", *, session="one", account="sample-account", timestamp=None, status="completed"):
    return {
        "id": f"synthetic-trace-{number}",
        "sessionId": session,
        "timestamp": timestamp,
        "metadata": {"account_uuid": account, "turn_number": number, "status": status},
        "input": {"user_prompt": prompt},
        "output": {"response": reply},
    }


class SessionLinksTests(unittest.TestCase):
    def analyze(self, *turns, include_review=True):
        return analyze(normalize_rows(turns), "local_json", include_review=include_review)

    def test_similar_requests_link_across_sessions_in_chronological_order(self):
        result = self.analyze(
            turn(1, "מה משפיע על שינוי ברוחב העורקים בעין?", session="two", timestamp="2025-01-02T00:00:00Z"),
            turn(2, "מה משפיע על שינוי רוחב העורקים?", timestamp="2025-01-01T00:00:00Z"),
        )
        link, = result["cross_session_links"]
        self.assertEqual((link["kind"], link["subject"]), ("repeated_request", "artery_width"))
        self.assertEqual(link["order"], "chronological")
        self.assertEqual([t["session_number"] for t in link["turns"]], [2, 1])
        self.assertEqual(result["cross_session"]["users_with_repeated_request"], 1)
        recurring, = result["user_summaries"][0]["recurring_topics"]
        self.assertEqual(recurring["subject"], "artery_width")
        self.assertEqual(recurring["links"][0]["turns"], link["turns"])
        self.assertNotIn("sample-account", json.dumps(result))
        self.assertNotIn("synthetic-trace", json.dumps(result))

    def test_same_subject_with_different_intents_is_not_a_repeat(self):
        link, = self.analyze(
            turn(1, "איך מצטבר שומן ויסצרלי?"),
            turn(2, "מה אפשר לעשות כדי להוריד שומן וויסצרלי?", session="two"),
        )["cross_session_links"]
        self.assertEqual((link["kind"], link["order"]), ("shared_subject", "unknown"))
        self.assertEqual(link["follow_through"], "unknown")

    def test_same_intent_without_wording_overlap_stays_a_weaker_link(self):
        link, = self.analyze(
            turn(1, "מה המשמעות של רוחב העורקים?"),
            turn(2, "מה אומר לי המדד הנוכחי של רוחב עורקי הרשתית?", session="two"),
        )["cross_session_links"]
        self.assertEqual(link["kind"], "same_intent_and_subject")
        self.assertNotIn("similar_prompt_tokens", link["evidence"])

    def test_unrelated_and_reply_only_subjects_do_not_link(self):
        result = self.analyze(
            turn(1, "מה אפשר לעשות כדי להוריד שומן ויסצרלי?"),
            turn(2, "מה זה TEM?", "האם הכוונה לשומן ויסצרלי?", session="two"),
            turn(3, "מה המשמעות של BMI?", session="three"),
        )
        self.assertEqual(result["cross_session_links"], [])
        self.assertEqual(result["user_summaries"][0]["recurring_topics"], [])

    def test_missing_ids_other_accounts_and_same_sessions_do_not_link(self):
        prompt = "מה משפיע על רוחב העורקים?"
        result = self.analyze(
            turn(1, prompt), turn(2, prompt),
            turn(3, prompt, session="two", account="other"),
            turn(4, prompt, session="three", account=None),
            turn(5, prompt, session=None),
        )
        self.assertEqual(result["cross_session"]["link_count"], 0)

    def test_one_link_per_subject_and_session_pair_uses_best_match(self):
        prompt = "מה המשמעות של רוחב העורקים?"
        result = self.analyze(
            turn(1, "מה משפיע על רוחב העורקים?"),
            turn(2, prompt),
            turn(3, prompt, session="two"),
        )
        link, = result["cross_session_links"]
        self.assertEqual(link["kind"], "repeated_request")
        self.assertEqual([t["recorded_turn"] for t in link["turns"]], [2, 3])

    def test_later_relevant_reply_is_separate_from_satisfaction(self):
        prompt = "מה משפיע על רוחב העורקים?"
        first = turn(1, prompt, timestamp="2025-01-01T00:00:00Z", status="error")
        second = turn(2, prompt, "רוחב העורקים קשור לכמה גורמים.", session="two", timestamp="2025-01-02T00:00:00Z")
        result = self.analyze(first, second)
        link, = result["cross_session_links"]
        self.assertEqual(link["follow_through"], "later_reply_addresses_topic")
        self.assertEqual(result["conversation"]["satisfaction"], {"unknown": 2})
        aggregate = self.analyze(first, second, include_review=False)
        self.assertEqual(aggregate["cross_session"], result["cross_session"])
        self.assertNotIn("cross_session_links", aggregate)

    def test_no_follow_through_without_reliable_order(self):
        prompt = "מה משפיע על רוחב העורקים?"
        result = self.analyze(
            turn(1, prompt, timestamp="2025-01-01T00:00:00Z", status="error"),
            turn(2, prompt, "הסבר על רוחב העורקים.", session="two", timestamp="2025-01-01T00:00:00Z"),
        )
        link, = result["cross_session_links"]
        self.assertEqual((link["order"], link["follow_through"]), ("unknown", "unknown"))
