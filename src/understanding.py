import re
from dataclasses import dataclass

from .conversation import TOPIC_REFERENCE, normalized
from .conversation_signals import adjacent, measures_in
from .models import Trace


METRIC_DOMAINS = {
    "hba1c": "blood_tests", "glucose": "blood_tests", "triglycerides": "blood_tests",
    "cholesterol": "blood_tests", "nightingale_panel": "blood_tests",
    "visceral_fat": "body_composition", "bmi": "body_composition",
    "body_fat_percent": "body_composition", "sleep_quality": "sleep", "sleep_apnea": "sleep",
    "rem_sleep": "sleep", "artery_width": "retina_imaging",
    "macula": "retina_imaging", "bone_density": "bone_density",
}
DOMAIN_CUES = {
    "blood_tests": r"בדיק(?:ת|ות) (?:ה)?דם|תוצאות (?:ה)?מעבדה",
    "body_composition": r"הרכב (?:ה)?גוף",
    "sleep": r"דוח (?:ה)?שינה|בדיקת (?:ה)?שינה|מדדי שינה",
    "retina_imaging": r"רשתית|קרקעית העין",
    "bone_density": r"צפיפות (?:ה)?עצם",
}
INTENT_CUES = (
    ("temporal_trend", r"השינוי ב|(?:עלייה|עליה|ירידה) בין|(?:עלה|ירד|השתפר|החמיר)\b|בין (?:ה)?בדיקות|לעומת (?:ה)?בדיקה קודמת|מאז הפעם הקודמת"),
    ("cohort_comparison", r"ביחס ל(?:משתתפים|אחרים)|בהשוואה ל(?:אחרים|משתתפים)|לעומת (?:משתתפים|אחרים)|מעל (?:הממוצע|רוב האנשים).{0,25}במחקר|איפה אני ממוקם"),
    ("literature_norm_comparison", r"ביחס לספרות|לפי הספרות|בספרות|טווח (?:ה)?נורמ[הא]|טווח (?:ה)?(?:רפואי )?תקין|ערכי ייחוס"),
    ("panel_breakdown", r"נתון[־ -]נתון|כל (?:ערך|מדד)|(?:כל|את) הפאנל.{0,40}(?:ותסביר|להסביר|לפענח)"),
    ("concept_explanation", r"\bמה (?:בכלל )?בודקים|\bמה (?:בכלל )?נבדק|\bאיך (?:עובדת|מתבצעת) הבדיקה|\bמה (?:זה|פירוש)\s+(?!אומר\b)\S+\s*[?.!]*$"),
)
SINGLE_VALUE = re.compile(
    r"האם\s+\d+(?:[.,]\d+)?\s+(?:זה\s+)?(?:תקין|חריג|גבוה|נמוך)|"
    r"מה (?:אומר|משמעות (?:ה)?(?:ערך|תוצאה))|מה (?:זה )?אומר אחוזון|האם זה תקין"
)
BROAD_INTERPRETATION = re.compile(
    r"מה (?:המשמעות|זה אומר)|איך (?:הבדיקות שלי|לקרוא את (?:הערך|התוצאה|הבדיקה|המדד))|"
    r"מה אומר(?:ת|ים)? (?:ה)?(?:בדיקה|מדדים|תוצאה|דוח)"
)
BACK_REFERENCE = re.compile(r"הפאנל|המדד הזה|הבדיקות|התוצאה|ביחס לאחרים|כל ערך|נתון[־ -]נתון|האם זה|מה זה אומר")
THIRD_PERSON_BMI = re.compile(r"(?:^|\s)ה?bmi\s+של\s+\S+")
QUESTION_OR_REQUEST = re.compile(
    r"\?|(?:^|[.!]\s+)(?:ו?מה|האם|איך|איפה|היכן|אפשר|הסבר|תסביר|שלוף|תפרט)\b"
)


@dataclass(frozen=True, slots=True)
class TestTarget:
    domain: str | None = None
    specific_metric: str | None = None


@dataclass(frozen=True, slots=True)
class UnderstandingRequest:
    understanding_intent: tuple[str, ...]
    test_target: TestTarget
    resolution_source: str
    target_position: int | None


def test_target(text: str) -> tuple[TestTarget, bool]:
    metrics = measures_in(text)
    if re.search(r"(?:^|\s)ה?bmi\b", text):
        metrics.add("bmi")
    if re.search(r"\brem\b|שנת rem|שנת חלום", text):
        metrics.add("rem_sleep")
    if re.search(r"מקולה|כתם צהוב|\bmacula\b", text):
        metrics.add("macula")
    # A specifically named panel stays the target when the text also lists its components.
    if "nightingale_panel" in metrics and metrics <= {"nightingale_panel", "cholesterol", "triglycerides", "glucose", "hba1c"}:
        metrics = {"nightingale_panel"}
    domains = {METRIC_DOMAINS[m] for m in metrics if m in METRIC_DOMAINS}
    domains.update(domain for domain, pattern in DOMAIN_CUES.items() if re.search(pattern, text))
    return TestTarget(
        next(iter(domains)) if len(domains) == 1 else None,
        next(iter(metrics)) if len(metrics) == 1 and len(domains) <= 1 else None,
    ), bool(metrics or domains)


@dataclass(slots=True)
class UnderstandingContext:
    previous: Trace | None = None
    anchor: TestTarget = TestTarget()
    anchor_position: int | None = None

    def inspect(self, trace: Trace, primary_intent: str, position: int) -> UnderstandingRequest | None:
        prompt = normalized(trace.prompt)
        direct, named = test_target(prompt)
        earlier = self.previous is not None and adjacent(self.previous, trace)
        points_back = bool(TOPIC_REFERENCE.search(prompt) or BACK_REFERENCE.search(prompt))
        if named:
            self.anchor, self.anchor_position = direct, position
            target, source, target_position = direct, "user_direct", position
        elif earlier and points_back and self.anchor_position is not None:
            target, source, target_position = self.anchor, "session_context", self.anchor_position
        else:
            self.anchor, self.anchor_position = TestTarget(), None
            target, source, target_position = TestTarget(), "unresolved", None
        self.previous = trace

        if not prompt or primary_intent in {"feedback", "access_and_identity"}:
            return None
        if not QUESTION_OR_REQUEST.search(prompt) and primary_intent not in {"interpret_measurement", "compare_or_benchmark"}:
            return None
        # State and ownership are separate. A third-person question can still
        # request interpretation of a recognizable measure.
        intents = [kind for kind, pattern in INTENT_CUES if re.search(pattern, prompt)]
        if ("panel_breakdown" not in intents and SINGLE_VALUE.search(prompt)
                or not intents and primary_intent == "interpret_measurement" and target.specific_metric):
            intents.append("interpret_single_value")
        if "concept_explanation" in intents and not (named or source == "session_context" or "בדיקה" in prompt):
            intents.remove("concept_explanation")
        if not intents and primary_intent == "general_explanation" and named:
            intents.append("concept_explanation")
        if not (intents or BROAD_INTERPRETATION.search(prompt) or THIRD_PERSON_BMI.search(prompt)
                or primary_intent in {"interpret_measurement", "compare_or_benchmark"}):
            return None
        if target == TestTarget() and source != "unresolved":
            # Multiple explicit candidates take precedence over reply wording.
            source, target_position = "unresolved", None
        elif source == "unresolved":
            agent_target, _ = test_target(normalized(trace.response))
            if agent_target != TestTarget():
                target, source, target_position = agent_target, "agent_inferred", position
        return UnderstandingRequest(tuple(dict.fromkeys(intents)), target, source, target_position)
