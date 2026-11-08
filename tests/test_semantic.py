import json
import unittest
from pathlib import Path

from src.analyzer import analyze
from src.fetcher import normalize_rows
from src.semantic import CandidateTurn, INTENT_EXAMPLES, TOPIC_EXAMPLES, LocalE5, SemanticError, discover


class ExampleEncoder:
    def encode(self, texts):
        labels = list(TOPIC_EXAMPLES) + [key for key in INTENT_EXAMPLES if key != "unknown"]
        known = {
            example: label for examples_by_label in (TOPIC_EXAMPLES, INTENT_EXAMPLES)
            for label, examples in examples_by_label.items() for example in examples
        }
        known["התעוררתי בחצות"] = "sleep"
        known["איך לקרוא את הערך הזה?"] = "interpret_measurement"
        known["הצמיד לא מעביר נתונים"] = "new"
        return [
            tuple(float(label == known.get(text)) for label in labels + ["new"])
            for text in texts
        ]


def row(number, prompt, *, account="one", turn=1):
    return {
        "id": f"fixture-private-id-{number}",
        "sessionId": f"fixture-private-session-{account}",
        "metadata": {"account_uuid": account, "turn_number": turn},
        "input": {"user_prompt": prompt},
    }


class SemanticTests(unittest.TestCase):
    def test_semantic_anchor_carries_to_next_turn_and_keeps_provenance(self):
        batch = normalize_rows([row(1, "התעוררתי בחצות"), row(2, "האם זה תקין?", turn=2)])
        result = analyze(batch, "local_json", include_review=True, encoder=ExampleEncoder())
        first, second = result["review"]
        self.assertEqual((first["topics"], first["topic_source"]), (("sleep",), "semantic"))
        self.assertEqual((second["topics"], second["topic_source"]), (("sleep",), "previous_user_turn"))
        self.assertEqual(second["topic_anchor_method"], "semantic")
        self.assertEqual(second["topic_anchor_position"], 1)
        self.assertIn("self_report", first["state_signals"])
        sleep = next(topic for topic in result["topics"] if topic["topic"] == "sleep")
        self.assertEqual((sleep["trace_count"], sleep["semantic_trace_count"]), (2, 1))
        self.assertEqual(result["topic_population"]["unclassified_trace_count"], 0)
        self.assertEqual(result["topic_population"]["semantic_trace_count"], 1)

    def test_semantic_intent_and_candidate_do_not_expose_prompt_or_ids(self):
        batch = normalize_rows([
            row(1, "איך לקרוא את הערך הזה?"),
            row(2, "הצמיד לא מעביר נתונים", account="a"),
            row(3, "הצמיד לא מעביר נתונים", account="b"),
            row(4, "הצמיד לא מעביר נתונים", account="c"),
        ])
        result = analyze(batch, "local_json", include_review=True, encoder=ExampleEncoder())
        self.assertEqual(result["review"][0]["intent_source"], "semantic")
        candidate, = result["topic_discovery"]["candidates"]
        self.assertEqual((candidate["trace_count"], candidate["user_count"]), (3, 3))
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("fixture-private", rendered)
        self.assertNotIn("הצמיד לא מעביר נתונים", rendered)

    def test_discovery_requires_distinct_users_and_pairwise_similarity(self):
        base = CandidateTurn("same", 1, 1, 1, (1.0, 0.0))
        same_owner = CandidateTurn("same", 1, 2, 1, (1.0, 0.0))
        second = CandidateTurn("second", 2, 1, 1, (0.92, 0.392))
        third = CandidateTurn("third", 3, 1, 1, (0.72, 0.694))
        result = discover([base, same_owner, second, third], include_review=True)
        self.assertEqual(result["candidates"], [])

    def test_missing_local_model_fails_without_attempting_a_download(self):
        with self.assertRaises(SemanticError):
            LocalE5(Path("missing-semantic-model-directory"))
