import re
from dataclasses import asdict, dataclass

from .conversation import TopicEvidence, TurnReview, normalized
from .models import Trace
from .request_coverage import RequestComponent
from .session_links import subjects_in


SUBJECT_NAMES = {
    "artery_width": "רוחב העורקים", "visceral_fat": "שומן ויסצרלי", "bmi": "BMI",
    "nightingale_panel": "פאנל Nightingale", "grip_strength": "כוח אחיזה",
    "bone_density": "צפיפות עצם", "sleep_apnea": "דום נשימה", "sleep_quality": "איכות השינה",
    "cholesterol": "כולסטרול", "sleep": "שינה", "blood_tests": "בדיקות דם",
    "body_composition": "הרכב גוף", "retinal_imaging": "בדיקות רשתית",
    "nutrition": "תזונה", "cardiovascular": "לחץ דם ודופק", "pain": "כאב",
    "general_health": "בריאות כללית",
}
FOCUS_TEXT = {
    "compare_change_with_cohort": "המשתמש התייחס לשינוי במדידה וביקש להבין אם הוא חריג ביחס לקבוצת המחקר.",
    "compare_with_cohort": "המשתמש ביקש להבין איך המדידה שלו עומדת ביחס לקבוצת ייחוס.",
    "interpret_change": "המשתמש התייחס לשינוי בין מדידות וביקש להבין את משמעותו.",
    "interpret_percentile": "המשתמש ביקש להבין את משמעות האחוזון.",
    "interpret_panel": "המשתמש ביקש להבין את הערכים בפאנל הבדיקות.",
    "interpret_measurement": "המשתמש ביקש לפרש מדידה.",
    "possible_action": "המשתמש בירר אם אפשר לנקוט צעד בעקבות המדידה או כדי לשפר אותה.",
    "clinical_question": "המשתמש שאל אם יש עדות למצב רפואי.",
    "sleep_interruption": "המשתמש דיווח על יקיצה במהלך הלילה.",
    "late_sleep_onset": "המשתמש דיווח שנרדם מאוחר.",
    "self_report": "המשתמש תיאר חוויה או מידע אישי.",
    "expressed_concern": "המשתמש הביע דאגה במפורש.",
}


@dataclass(frozen=True, slots=True)
class FindingEvidence:
    session_number: int
    position: int
    recorded_turn: int | None
    subject_position: int | None
    repeat_position: int | None


@dataclass(frozen=True, slots=True)
class FindingKey:
    signal: str
    focus: str
    subject: str | None
    subject_source: str


def focus_for(signal: str, prompt: str, subject: str | None) -> str:
    if signal == "measurement_question":
        change = bool(re.search(r"שינוי|עלייה|עלי[הת]\s+(?:בין|ב)|יריד[הת]|בין הבדיקות", prompt))
        cohort = bool(re.search(r"ביחס ל(?:משתתפים|אחרים)|בהשוואה לאחרים|כמה זה נורמלי", prompt))
        if cohort:
            return "compare_change_with_cohort" if change else "compare_with_cohort"
        if change:
            return "interpret_change"
        if "אחוזון" in prompt:
            return "interpret_percentile"
        if subject == "nightingale_panel":
            return "interpret_panel"
        return "interpret_measurement"
    if signal == "self_report":
        if re.search(r"(?:קמתי|התעוררתי).*(?:לילה|בוקר)|(?:לילה|הלילה).*(?:קמתי|התעוררתי)", prompt):
            return "sleep_interruption"
        if "נרדמתי" in prompt and re.search(r"מאוחר|לפנות בוקר|רק בבוקר", prompt):
            return "late_sleep_onset"
    return {
        "action_goal": "possible_action", "clinical_concern": "clinical_question",
    }.get(signal, signal)


def collect_findings(
    groups: dict[FindingKey, list[FindingEvidence]], trace: Trace, review: TurnReview,
    components: tuple[RequestComponent, ...], session_number: int, position: int,
    topic: TopicEvidence, subjects_by_position: dict[int, tuple[str, ...]],
) -> None:
    if not review.state_signals:
        return
    prompt = normalized(trace.prompt)
    anchor_position = topic.anchor_position
    direct_subject = subjects_by_position[position]
    anchor_subject = subjects_by_position.get(anchor_position, ()) if anchor_position is not None else ()
    earlier = anchor_position is not None and anchor_position != position
    subject, source, subject_position = None, "unknown", None
    if len(direct_subject) == 1:
        subject, source, subject_position = direct_subject[0], "user_prompt", position
    elif not direct_subject and len(anchor_subject) == 1 and earlier:
        subject, source, subject_position = anchor_subject[0], "earlier_user_prompt", anchor_position
    elif not direct_subject and not anchor_subject:
        agent_subjects = subjects_in(normalized(trace.response))
        if len(agent_subjects) == 1 and any(s in review.state_signals for s in ("measurement_question", "clinical_concern")):
            subject, source, subject_position = agent_subjects[0], "agent_reply", position
        elif len(topic.labels) == 1:
            subject, source, subject_position = topic.labels[0], (
                "semantic_topic" if topic.method == "semantic" else
                "earlier_user_prompt" if earlier else "user_prompt"
            ), anchor_position

    incomplete = review.delivery in {"open", "partial"} or any(
        part.status in {"reported_blocker", "execution_error", "omitted_without_explanation"}
        for part in components
    )
    for signal in review.state_signals:
        # Agent context is only useful for interpreting a question. It cannot
        # supply the subject of a separate self-report or expressed concern.
        if source == "agent_reply" and signal not in {"measurement_question", "clinical_concern"}:
            key = FindingKey(signal, focus_for(signal, prompt, None), None, "unknown")
            evidence_position = None
        else:
            key = FindingKey(signal, focus_for(signal, prompt, subject), subject, source)
            evidence_position = subject_position
        groups.setdefault(key, []).append(FindingEvidence(
            session_number, position, trace.turn_number, evidence_position,
            position + 1 if incomplete and review.next_user_action == "repeated" else None,
        ))


def summarize_findings(groups: dict[FindingKey, list[FindingEvidence]]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for key, references in groups.items():
        text = FOCUS_TEXT[key.focus]
        if key.subject:
            name = SUBJECT_NAMES[key.subject]
            if key.subject_source == "agent_reply":
                text += f" בתשובה, הסוכן התייחס לנושא {name}."
            elif key.subject_source == "semantic_topic":
                text += f" הנושא המשוער לפי הסיווג המקומי הוא {name}."
            else:
                text += f" הנושא שעלה מדברי המשתמש הוא {name}."
        repeated = any(ref.repeat_position is not None for ref in references)
        if repeated:
            text += " המשתמש חזר על השאלה אחרי מענה חסר."
        findings.append({
            **asdict(key), "summary_he": text,
            "repeated_after_incomplete_reply": repeated,
            "evidence": [asdict(ref) for ref in references],
        })
    return findings
