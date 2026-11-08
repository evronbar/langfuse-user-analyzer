import json
import unittest

from src.analyzer import analyze
from src.fetcher import normalize_rows


def turn(number, prompt, reply=""):
    return {
        "id": f"synthetic-id-{number}", "sessionId": "synthetic-session",
        "metadata": {"account_uuid": "synthetic-account", "turn_number": number, "status": "completed"},
        "input": {"user_prompt": prompt}, "output": {"response": reply},
    }


def analyze_rows(*rows):
    return analyze(normalize_rows(rows), "local_json", include_review=True)


class StateFindingsTests(unittest.TestCase):
    def test_repeated_benchmark_finding_keeps_both_evidence_references(self):
        prompt = "מבחינת השינוי בשומן הוויסצרלי, איפה אני ממוקם ביחס למשתתפים?"
        result = analyze_rows(turn(1, prompt, "האחוזון אינו נגיש."), turn(2, prompt))
        finding, = result["user_summaries"][0]["state_findings"]
        self.assertEqual(finding["focus"], "compare_change_with_cohort")
        self.assertEqual(finding["subject"], "visceral_fat")
        self.assertTrue(finding["repeated_after_incomplete_reply"])
        self.assertEqual([row["position"] for row in finding["evidence"]], [1, 2])
        self.assertEqual(finding["evidence"][0]["repeat_position"], 2)
        self.assertEqual(result["state_finding_users_by_focus"], {"compare_change_with_cohort": 1})
        self.assertNotIn(prompt, json.dumps(result, ensure_ascii=False))

    def test_agent_subject_is_attributed_and_does_not_become_user_topic(self):
        result = analyze_rows(turn(
            1, "מה המשמעות של העליה בין הבדיקות?",
            "מדובר בצפיפות העצם. המדידה האחרונה היא 731.22.",
        ))
        finding, = result["user_summaries"][0]["state_findings"]
        self.assertEqual((finding["focus"], finding["subject"], finding["subject_source"]),
                         ("interpret_change", "bone_density", "agent_reply"))
        self.assertEqual(result["review"][0]["topics"], ())
        self.assertNotIn("731.22", json.dumps(result))
        self.assertIn("הסוכן", finding["summary_he"])

    def test_panel_follow_up_is_interpretation_with_user_anchor(self):
        result = analyze_rows(
            turn(1, "איפה אני רואה את פאנל Nightingale?"),
            turn(2, "שלוף את כל הפאנל ותסביר"),
        )
        finding, = result["user_summaries"][0]["state_findings"]
        self.assertEqual((finding["focus"], finding["subject_source"]), ("interpret_panel", "earlier_user_prompt"))
        self.assertEqual(finding["evidence"][0]["subject_position"], 1)
        self.assertEqual(finding["evidence"][0]["position"], 2)

    def test_hypothetical_concern_and_third_person_measurement_are_excluded(self):
        result = analyze_rows(
            turn(1, "איך היית מסביר מה יקרה אם אני מודאגת?"),
            turn(2, "איך הBMI של דנה?"),
        )
        self.assertEqual(result["user_summaries"][0]["state_findings"], [])

    def test_first_person_experience_and_concern_are_separate_findings(self):
        result = analyze_rows(turn(1, "נרדמתי רק לפנות בוקר. אני מפחדת שזה יחזור."))
        findings = result["user_summaries"][0]["state_findings"]
        self.assertEqual([finding["focus"] for finding in findings], ["late_sleep_onset", "expressed_concern"])
