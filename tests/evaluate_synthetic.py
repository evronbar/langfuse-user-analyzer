"""Generate and score a reproducible synthetic dataset from the project root."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.analyzer import analyze
from src.fetcher import normalize_rows
from src.semantic import LocalE5


def generate():
    traces, expected = [], {}
    anchor = datetime(2025, 3, 5, tzinfo=UTC)

    def add(user, session, turn, prompt, response="נרשם מענה.", *, topic=None, intent=None, novel=None, status="completed"):
        key = f"synthetic-{user}-{session}-{turn}"
        traces.append({
            "id": key,
            "sessionId": f"synthetic-session-{user}-{session}",
            "timestamp": (anchor + timedelta(hours=2 * user + session, minutes=turn)).isoformat(),
            "metadata": {"account_uuid": f"synthetic-user-{user}", "turn_number": turn, "status": status},
            "input": {"user_prompt": prompt},
            "output": {"response": response},
            "totalCost": round(0.03 * (1 + user % 4), 2),
            "latency": 1.5 + turn,
        })
        expected[key] = {"topic": topic, "intent": intent, "novel": novel}

    # These prompts use different wording from the model's reference examples.
    add(1, 1, 1, "הלילה קמתי כל שעה עד הבוקר", topic="sleep")
    add(1, 1, 2, "האם זה תקין?", topic="sleep", intent="interpret_measurement")
    add(1, 1, 3, "זה עזר", "בשמחה", intent="feedback")
    add(2, 1, 1, "נרדמתי רק לפנות בוקר ובמשך היום אני גמורה", topic="sleep")
    add(3, 1, 1, "שלחו לי תוצאות מהמעבדה, איך אני אמורה לקרוא את הערכים שם?", topic="blood_tests", intent="interpret_measurement")
    add(4, 1, 1, "האם הכמות של השומן סביב הבטן השתנתה מאז הבדיקה הקודמת?", topic="body_composition", intent="interpret_measurement")
    add(5, 1, 1, "בצילום של קרקעית העין רואים הבדלים בכלי הדם. איך מפרשים את זה?", topic="retinal_imaging", intent="interpret_measurement")
    add(6, 1, 1, "הדינמומטר ביד נתן לי תוצאה נמוכה ואני רוצה להתחזק", topic="grip_strength", intent="next_action")
    add(7, 1, 1, "אני מנסה לברר אם התוצאה שלי מעל רוב האנשים במחקר", intent="compare_or_benchmark")
    add(8, 1, 1, "יש משהו שאוכל לשנות ביום יום כדי שתוצאת הבדיקה תשתפר?", intent="next_action")
    add(9, 1, 1, "אשמח לדעת איפה נפתח המסמך המלא בתוך החשבון", intent="retrieve_or_locate")
    add(10, 1, 1, "מה זה RZQ?", "אולי זה קשור לשינה", intent="define_term")
    add(11, 1, 1, "השעון החכם לא מסתנכרן עם האפליקציה", novel="device_sync")
    add(12, 1, 1, "איך מחברים את השעון החכם? הסנכרון עם האפליקציה נתקע", novel="device_sync")
    add(13, 1, 1, "האפליקציה לא מקבלת נתונים מהשעון החכם, יש בעיה בסנכרון", novel="device_sync")
    add(14, 1, 1, "שגיאת סנכרון בין השעון החכם לבין האפליקציה", novel="device_sync")
    add(15, 1, 1, "אני רוצה להזיז את מועד הפגישה שנקבעה לי")
    add(16, 1, 1, "למה דף החשבון נתקע אחרי הזנת הקוד?")
    add(17, 1, 1, "איפה אני רואה את פאנל Nightingale?", "אוכל להציג אותו בשיחה הזו.", topic="blood_tests", intent="retrieve_or_locate")
    add(17, 1, 2, "שלוף את כל הפאנל", "", topic="blood_tests", intent="retrieve_or_locate", status="error")
    add(17, 1, 3, "הסבר כל ערך", "", topic="blood_tests", intent="interpret_measurement", status="error")
    add(18, 1, 1, "מה משפיע על שינוי רוחב העורקים?", topic="retinal_imaging", intent="general_explanation")
    add(18, 2, 1, "מה משפיע על שינוי ברוחב העורקים בעין?", topic="retinal_imaging", intent="general_explanation")
    add(19, 1, 1, "מה המשמעות של BMI 24? זה מדאיג אותי", "כדאי לפנות לרופא המטפל.", topic="body_composition", intent="interpret_measurement")
    add(20, 1, 1, "מה אומר האחוזון שלי?", "לא הצלחתי למצוא את ההשוואה.", intent="compare_or_benchmark")
    add(20, 1, 2, "מה אומר האחוזון שלי?", "האחוזון עדיין אינו נגיש.", intent="compare_or_benchmark")
    add(21, 1, 1, "מה אומרת בדיקת דם בהשוואה למדדי שינה?")
    add(21, 1, 2, "האם זה תקין?", intent="interpret_measurement")
    expected["synthetic-21-1-1"]["skip_topic"] = True
    return traces, expected


def score(result, traces, expected):
    # Match run-local review coordinates without disclosing source IDs in analysis.
    from src.analyzer import grouped_traces

    ids = [
        t.trace_id
        for sessions in grouped_traces(normalize_rows(traces))
        for session in sessions
        for t in session
    ]
    review = list(zip(ids, result["review"], strict=True))
    by_position = {(r["user_number"], r["session_number"], r["position"]): key for key, r in review}
    topics, intents = [], []
    for key, row in review:
        gold = expected[key]
        if not gold.get("skip_topic"):
            topics.append((key, gold["topic"], tuple(row["topics"])))
        if gold["intent"] is not None:
            intents.append((key, gold["intent"], row["intent"]))
    topic_errors = [
        {"row": key, "expected": gold, "actual": list(actual)}
        for key, gold, actual in topics if actual != ((gold,) if gold else ())
    ]
    intent_errors = [
        {"row": key, "expected": gold, "actual": actual}
        for key, gold, actual in intents if gold != actual
    ]
    novel = {key for key, gold in expected.items() if gold["novel"]}
    discovered = []
    for candidate in result.get("topic_discovery", {}).get("candidates", []):
        members = {
            by_position[r["user_number"], r["session_number"], r["position"]]
            for r in candidate["evidence"]
        }
        discovered.append({
            "size": len(members), "device_sync_members": len(members & novel), "rows": sorted(members),
        })
    assert result["summary"]["trace_count"] == len(traces)
    assert result["summary"]["user_count"] == 21
    assert result["summary"]["session_count"] == 22
    assert result["user_dynamics"]["single_observed_turn"]["count"] == 18
    assert result["user_dynamics"]["inter_session_hours"]["count"] == 1
    assert sum(result["time_distribution"]["traces_by_hour"]) == len(traces)
    assert result["summary"]["error_trace_count"] == 2
    assert result["friction_metrics"]["users_with_errors"] == 1
    assert result["clinical_signals"]["numeric_measurement_mention"]["count"] == 1
    assert result["friction_metrics"]["doctor_referral_wording"]["count"] == 1
    assert result["friction_metrics"]["exact_repeat_count"] == 1
    assert result["conversation"]["satisfaction"]["positive"] == 1
    assert result["cross_session"]["users_with_repeated_request"] == 1
    assert result["cost_and_latency"]["top_10_percent_user_cost"]["eligible_user_count"] == 21
    assert result["trace_cost_usd"]["count"] == len(traces)
    assert result["trace_latency_seconds"]["count"] == len(traces)
    assert result["state_users"]["expressed_concern"] == 1
    return {
        "topic_correct": len(topics) - len(topic_errors), "topic_total": len(topics),
        "known_topic_correct": sum(gold is not None and actual == (gold,) for _, gold, actual in topics),
        "known_topic_total": sum(gold is not None for _, gold, _ in topics),
        "unexpected_topic_count": sum(gold is None and bool(actual) for _, gold, actual in topics),
        "intent_correct": len(intents) - len(intent_errors), "intent_total": len(intents),
        "topic_errors": topic_errors, "intent_errors": intent_errors,
        "discovered": discovered,
        "validated": [
            "ingestion", "session_counts", "session_gaps", "hourly_counts", "numeric_measurement",
            "expressed_concern", "error_concentration", "doctor_referral", "exact_repeat",
            "explicit_feedback", "cross_session_repeat", "cost_coverage", "latency_coverage",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantic-model", type=Path)
    args = parser.parse_args()
    traces, expected = generate()
    args.output.mkdir(parents=True, exist_ok=True)
    batch = normalize_rows(traces)
    baseline = analyze(batch, "local_json", include_review=True)
    results = {"dataset": baseline["summary"], "rules": score(baseline, traces, expected)}
    if args.semantic_model:
        result = analyze(batch, "local_json", include_review=True, encoder=LocalE5(args.semantic_model))
        results["semantic"] = score(result, traces, expected)
    for filename, payload in (
        ("synthetic-traces.json", traces), ("synthetic-expected.json", expected), ("synthetic-results.json", results),
    ):
        (args.output / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
