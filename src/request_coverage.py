import re
from dataclasses import dataclass
from typing import Literal

from .conversation import normalized
from .models import Trace


CoverageStatus = Literal[
    "addressed", "reported_blocker", "stated_boundary",
    "omitted_without_explanation", "execution_error", "unknown",
]

BLOCKER = r"\b(?:לא הצלחתי|לא הצליח(?:ה)?|אינו נגיש|לא נגיש|לא זמין|לא נמצא בידי|לא ניתן לשלוף|נגמרו לי הסיבובים)\b"
BOUNDARY = r"\b(?:לא יכול|אינני יכול|איני יכול|לא אוכל|אסור לי|אין באפשרותי|אינני רופא|איני רופא|אני לא רופא)\b"
OWNER_CHOICE = re.compile(r"(?:זה|הנתונים|המדד|התוצאה)\s+של\s+([א-ת]+)\s+או\s+([א-ת]+)")


@dataclass(frozen=True, slots=True)
class ComponentRule:
    kind: str
    request: str
    answer: str
    scope: str


RULES = (
    ComponentRule(
        "locate_panel", r"(?:איפה|היכן)\b.{0,45}\b(?:פאנל|הפאנל|nightingale)\b",
        r"(?:באפליקציה\b.{0,60}(?:מסך|תפריט|לשונית)|(?:ב|מתוך)תפריט\b|https?://"
        r"|(?:פאנל|nightingale)\b[^.!?\n]{0,75}\bזמין\b[^.!?\n]{0,45}\b(?:כאן|בשיחה (?:זו|הזו))\b)",
        r"(?:פאנל|גישה|מיקום|מסך)",
    ),
    ComponentRule(
        "retrieve_panel", r"\b(?:שלוף|תשלוף|הצג|תציג|תראה לי|תן לי)\b.{0,42}\b(?:הפאנל|פאנל|nightingale)\b",
        r"(?:^|\n)\s*(?:[-*]\s*)?[^:\n|]{2,45}:\s*(?:\*{1,2}\s*)?\d",
        r"(?:לשלוף|להציג|להביא|לגשת|שליפ[הת]|גישה)",
    ),
    ComponentRule(
        "explain_values", r"(?:ותסביר\b|\b(?:הסבר|תסביר)(?: לי)?\s+(?:נתון נתון|כל (?:ערך|מדד)|מה אומר))",
        r"\b(?:המשמעות היא|הערך מייצג|מדד זה בוחן|פירושו|הנתון מתאר|משקף את"
        r"|מצביע(?:ה)? על(?: כך)?|מעיד(?:ה)? על|התוצאה היא|טווח תקין)\b|\bמראה ש(?=[א-ת\s])",
        r"(?:להסביר|הסבר|משמעות|לפרש|פירוש)",
    ),
    ComponentRule(
        "compare_cohort", r"\b(?:ביחס למשתתפים|ביחס לאחרים|בהשוואה לאחרים|לעומת משתתפים|איפה אני ממוקם|(?:מה|איפה) האחוזון שלי)\b",
        r"(?:אחוזון\s*(?:של\s*)?\d{1,3}\b|(?:מעל|מתחת ל)[־\s]*\d{1,3}%\s*(?:מהמשתתפים|מהקבוצה))",
        r"(?:אחוזו[ןנ]|קוהורט|השוואה|קבוצת המחקר)",
    ),
    ComponentRule(
        "check_measurement", r"\b(?:האם כדאי לי לבדוק|כדאי להיבדק|כדאי לבדוק שוב)\b",
        r"\b(?:כדאי (?:לך )?(?:להיבדק|לחזור על הבדיקה)|בדיקה חוזרת עשויה)\b",
        r"(?:להיבדק|לבדוק|בדיקה רפואית|בדיקה חוזרת)",
    ),
    ComponentRule(
        "suggest_action", r"\b(?:מה (?:ניתן|אפשר|כדאי) לעשות|דרך להשפיע|איך (?:לשפר|להוריד|להפחית))\b",
        r"\b(?:אפשר להתחיל|אפשר לנסות|דרכים מוכרות|תרגילים|צעדים אפשריים)\b",
        r"(?:לייעץ|להמליץ|המלצה|ייעוץ|לשפר|להפחית|צעדים)",
    ),
    ComponentRule(
        "clinical_assessment", r"\b(?:האם יש (?:לי |עדות ל)|האם אני סובל)",
        r"\b(?:אין (?:כרגע )?(?:עדות|ממצאים המעידים) ל(?=[א-ת\s])"
        r"|לא נמצאה עדות ל(?=[א-ת\s])|נמצאה עדות ל(?=[א-ת\s])"
        r"|(?:ה?תסמינים|ה?סימנים) (?:עשויים להתאים|מתאימים|אינם מתאימים) ל(?=[א-ת\s])"
        r"|(?:ה?מדד|ה?ערך|ה?ממצא|התוצאה)[^.!?]{0,35}\b"
        r"(?:אינ(?:ו|ה) מעיד(?:ה)? בהכרח|(?:לא|אינ(?:ו|ה)) בהכרח מעיד(?:ה)?"
        r"|(?:לא|אינ(?:ו|ה)) מעיד(?:ה)?) על\b)",
        r"(?:אבחנה|לאבחן|רפואית|לייעץ)",
    ),
)


@dataclass(frozen=True, slots=True)
class RequestComponent:
    kind: str
    status: CoverageStatus
    evidence: str


def near_in_same_clause(clause: str, signal: str, scope: str) -> bool:
    return bool(re.search(
        rf"(?:{signal})[^.!?\n]{{0,80}}(?:{scope})|(?:{scope})[^.!?\n]{{0,80}}(?:{signal})", clause,
    ))


def status_for(
    trace: Trace, reply: str, rule: ComponentRule, delivery_reason: str, single: bool,
) -> tuple[CoverageStatus, str]:
    if not reply:
        if trace.status == "error":
            return "execution_error", "error_without_reply"
        return "unknown", "no_nonblank_reply"
    raw_clauses = re.split(r"(?<=[.!?])\s+|[\r\n]+", trace.response or "")
    clauses = [normalized(clause) for clause in raw_clauses]
    # A separate answer can coexist with a limitation. Ignore cues inside refusals and questions.
    matches = sum(
        len(re.findall(rule.answer, clause)) for clause in clauses
        if not clause.endswith("?") and not re.search(rf"{BLOCKER}|{BOUNDARY}", clause)
    )
    minimum = 2 if rule.kind == "retrieve_panel" else 1
    if matches >= minimum:
        return "addressed", "component_answer_cue"
    if any(near_in_same_clause(clause, BLOCKER, rule.scope) for clause in clauses):
        return "reported_blocker", "reported_blocker_near_component"
    if any(near_in_same_clause(clause, BOUNDARY, rule.scope) for clause in clauses):
        return "stated_boundary", "stated_boundary_near_component"
    if single and delivery_reason in {"reported_unavailability", "reported_unavailability_and_alternative_data"}:
        return "reported_blocker", "reported_blocker_in_single_component_reply"
    if single and delivery_reason in {"stated_boundary", "boundary_with_measurements"}:
        return "stated_boundary", "stated_boundary_in_single_component_reply"
    return "unknown", "component_coverage_unclear"


def review_components(trace: Trace, delivery_reason: str) -> tuple[RequestComponent, ...]:
    prompt, reply = normalized(trace.prompt), normalized(trace.response)
    rules = [rule for rule in RULES if re.search(rule.request, prompt)]
    owner = OWNER_CHOICE.search(prompt)
    components: list[RequestComponent] = []
    single = len(rules) + bool(owner) == 1
    for rule in rules:
        status, evidence = status_for(trace, reply, rule, delivery_reason, single)
        components.append(RequestComponent(rule.kind, status, evidence))

    if owner:
        if not reply and trace.status == "error":
            components.append(RequestComponent("identify_owner", "execution_error", "error_without_reply"))
        elif re.search(BLOCKER, reply):
            components.append(RequestComponent("identify_owner", "reported_blocker", "reported_blocker_in_ownership_reply"))
        elif re.search(BOUNDARY, reply):
            components.append(RequestComponent("identify_owner", "stated_boundary", "stated_boundary_in_ownership_reply"))
        else:
            named = [
                name for name in owner.groups()
                if re.search(rf"(?<!לא )\b(?:של|שייכים ל|שייכת ל)\s*{re.escape(name)}\b", reply)
            ]
            if len(named) == 1:
                components.append(RequestComponent("identify_owner", "addressed", "one_requested_owner_named"))
            elif not named and re.search(r"שייכ(?:ים|ת) לחשבון|משויך לחשבונך|הנתונים (?:הם )?שלך", reply):
                components.append(RequestComponent(
                    "identify_owner", "omitted_without_explanation", "account_answer_without_requested_identity",
                ))
            else:
                components.append(RequestComponent("identify_owner", "unknown", "component_coverage_unclear"))

    # Silence is only classified as omission if another requested component
    # was answered and the reply provides no blocker or refusal to attribute.
    if any(part.status == "addressed" for part in components) and not re.search(rf"{BLOCKER}|{BOUNDARY}", reply):
        components = [
            RequestComponent(part.kind, "omitted_without_explanation", "another_component_answered")
            if part.status == "unknown" and part.kind in {"retrieve_panel", "explain_values"}
            else part
            for part in components
        ]
    return tuple(components)
