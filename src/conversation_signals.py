import re
from dataclasses import dataclass, field

from .conversation import normalized, topics_in
from .metrics import CONCERN_WORDING, DOCTOR_REFERRAL, URGENCY_WORDING
from .models import Trace
from .session_links import subjects_in


MEASURES = {
    "hba1c": r"\b(?:hba1c|a1c)\b|המוגלובין מסוכרר",
    "glucose": r"\b(?:גלוקוז|סוכר (?:בדם|בצום))\b",
    "triglycerides": r"טריגליצרידים",
    "blood_pressure": r"לחץ (?:ה)?דם",
    "eye_pressure": r"לחץ תוך[־\- ]עיני",
    "body_fat_percent": r"אחוז (?:ה)?שומן",
    "pulse": r"\bדופק\b",
}
NUMBER = r"\d+(?:[.,]\d+)?"
MEASUREMENT_VALUES = re.compile(
    rf"(?<!\w)({NUMBER})\s*(?:%|mg\s*/\s*dl\b|mmol\s*/\s*l\b|bpm\b|"
    r"ק[״\"]?ג\b|גרם\b|שעות\b|דקות\b|ממ[״\"]?כ\b)"
    rf"|\b(?:hba1c|a1c|bmi|אחוזון|גלוקוז)\s*(?:הוא|של|:|=)?\s*({NUMBER})(?![\d/])"
)
BARE_NUMBER = re.compile(rf"^(?:(?:יצא|יצאה|קיבלתי|יש|נמדד) לי\s+)?({NUMBER})[?.!]*$")
TIME_QUESTION = re.compile(r"עלה או ירד|עלייה|העליה|ירידה|שיפור מאז|החמרה מאז|בין הבדיקות|לעומת הפעם הקודמת|בהשוואה לבדיקה הקודמת")
TIME_MARKER = re.compile(r"ביקור|בדיקה ה?(?:קודמת|אחרונה)|בעבר|לעומת|בהשוואה|מאז|20\d{2}")
CLARIFICATION = re.compile(r"מה היחידות|באילו יחידות|איזה מדד|באיזה מדד|למה התכוונת|אפשר לפרט|תוכל לפרט|תוכלי לפרט|האם התכוונת")
DISTRESS = re.compile(r"אני בסכנה|זה מפחיד אותי|הרופא הלחיץ אותי|אדום בבדיקה|(?:זה|הערך) חריג מאוד|זה מסוכן\?")
MEDICATION_REQUEST = re.compile(
    r"כמה (?:כדורים|טבליות) לקחת|(?:מה|איזה) מינון|(?:להפסיק|להתחיל|לשנות).{0,25}(?:סטטינים|תרופ[הת]|כדור|מינון)"
    r"|(?:איזה|איזו) תוסף|(?:לקחת|ליטול).{0,15}(?:תרופה|תוסף|כדור)"
)
CROSS_DOMAIN_RELATION = re.compile(r"האם.{0,80}(?:גרם|השפיע|העלה|הוריד|קשור)|יש קשר|מה הקשר|איך.{0,60}משפיע")
DISCLAIMS = re.compile(r"אינני רופא|אני לא רופא|לא יכול לייעץ|אינני יכול לייעץ|לא מהווה ייעוץ רפואי")
PUSHBACK = re.compile(r"אתה לא עונה|לא ענית|זה לא עונה|אני יודע(?:ת)? שאת(?:ה)? לא רופא|כבר שאלתי")
BRIEF_REQUEST = re.compile(r"בקצרה|תענה בקיצור|אפשר קצר יותר|תסכם במשפט")
TERM_QUESTION = re.compile(r"^(?:מה זה|מהו|מה פירוש)\s+([a-z][a-z0-9-]{1,15}|[א-ת]{3,20})[?.!]*$")
RETINA_EXPLANATION = re.compile(r"מה (?:זה|בודקים|המשמעות|אומר)|מה משפיע|איך (?:קוראים|מפרשים)")


def measures_in(text: str) -> set[str]:
    return set(subjects_in(text)) | {name for name, pattern in MEASURES.items() if re.search(pattern, text)}


def values_in(text: str) -> set[str]:
    return {next(value for value in match.groups() if value).replace(",", ".") for match in MEASUREMENT_VALUES.finditer(text)}


def domains_in(text: str) -> set[str]:
    domains = set(topics_in(text)) - {"general_health"}
    if re.search(r"גלוקוז|סוכר|hba1c|טריגליצרידים", text):
        domains.add("blood_tests")
    if re.search(r"צילום עין|קרקעית העין|לחץ תוך[־\- ]עיני", text):
        domains.add("retinal_imaging")
    return domains


def adjacent(first: Trace, second: Trace | None) -> bool:
    if second is None or not first.account_id or not first.session_id:
        return False
    if first.account_id != second.account_id or first.session_id != second.session_id:
        return False
    if first.turn_number is not None and second.turn_number is not None:
        return second.turn_number == first.turn_number + 1
    return bool(first.timestamp and second.timestamp and first.timestamp < second.timestamp)


@dataclass(frozen=True, slots=True)
class ConversationSignal:
    kind: str
    observation: str
    related_position: int | None = None
    categories: tuple[str, ...] = ()


@dataclass(slots=True)
class SessionSignals:
    user_measures: set[str] = field(default_factory=set)
    agent_measures: set[str] = field(default_factory=set)
    user_values: set[str] = field(default_factory=set)
    agent_values: set[str] = field(default_factory=set)
    previous: Trace | None = None

    def inspect(self, trace: Trace, following: Trace | None, position: int) -> list[ConversationSignal]:
        prompt, reply = normalized(trace.prompt), normalized(trace.response)
        next_prompt = normalized(following.prompt) if following is not None and adjacent(trace, following) else ""
        prompt_measures, reply_measures = measures_in(prompt), measures_in(reply)
        prompt_values, reply_values = values_in(prompt), values_in(reply)
        new_measures = reply_measures - prompt_measures - self.user_measures - self.agent_measures
        new_values = reply_values - prompt_values - self.user_values - self.agent_values
        results = []
        if prompt and (new_measures or new_values):
            results.append(ConversationSignal("agent_introduced_measurement", (
                "new_metric_and_value" if new_measures and new_values else "new_metric" if new_measures else "new_value"
            ), categories=tuple(sorted(new_measures))))

        if BARE_NUMBER.fullmatch(prompt):
            if CLARIFICATION.search(reply):
                resolution = "asked_for_clarification"
            elif len(self.user_measures) == 1:
                resolution = "prior_user_metric_available"
            elif reply_measures:
                resolution = "agent_named_metric_without_user_context"
            else:
                resolution = "metric_still_unclear"
            results.append(ConversationSignal("ambiguous_numeric_input", resolution))

        if TIME_QUESTION.search(prompt):
            if not reply:
                resolution = "no_reply"
            elif TIME_MARKER.search(reply) and len(reply_values) >= 2:
                resolution = (
                    "time_and_values_also_mentioned_by_user" if reply_values <= (prompt_values | self.user_values)
                    else "time_and_new_values_in_reply"
                )
            elif TIME_MARKER.search(reply):
                resolution = "time_referenced_without_two_recognized_values"
            else:
                resolution = "no_temporal_comparison_detected"
            results.append(ConversationSignal("temporal_question", resolution))

        if CONCERN_WORDING.search(prompt) or URGENCY_WORDING.search(prompt) or DISTRESS.search(prompt):
            results.append(ConversationSignal("concern_or_urgency_wording", (
                "reply_mentions_doctor" if DOCTOR_REFERRAL.search(reply) else "reply_present" if reply else "no_reply"
            )))
        if MEDICATION_REQUEST.search(prompt):
            results.append(ConversationSignal("medication_or_supplement_request", (
                "reply_states_boundary_or_referral" if DISCLAIMS.search(reply) or DOCTOR_REFERRAL.search(reply)
                else "reply_present" if reply else "no_reply"
            )))

        domains = domains_in(prompt)
        if "retinal_imaging" in domains and RETINA_EXPLANATION.search(prompt):
            results.append(ConversationSignal("retina_explanation_request", "explanation_requested"))
        if len(domains) > 1:
            results.append(ConversationSignal("cross_domain_request", (
                "relationship_question" if CROSS_DOMAIN_RELATION.search(prompt) else "multiple_domains_mentioned"
            ), categories=tuple(sorted(domains))))

        if next_prompt and (DISCLAIMS.search(reply) or DOCTOR_REFERRAL.search(reply)):
            if PUSHBACK.search(next_prompt):
                results.append(ConversationSignal("disclaimer_follow_up", "explicit_pushback", position + 1))
            elif next_prompt == prompt:
                results.append(ConversationSignal("disclaimer_follow_up", "exact_request_repeated", position + 1))
        if prompt and len(prompt.split()) <= 12 and len(reply.split()) >= 200:
            shorter = bool(BRIEF_REQUEST.search(next_prompt))
            results.append(ConversationSignal("long_reply_to_short_prompt", (
                "user_asked_for_brevity" if shorter else "length_gap_observed"
            ), position + 1 if shorter else None))
        if reply and trace.status == "error":
            results.append(ConversationSignal("nonempty_reply_with_error", "error_recorded_with_reply_text"))
        term = TERM_QUESTION.fullmatch(prompt)
        if term and self.previous is not None and adjacent(self.previous, trace):
            word = re.escape(term[1])
            if re.search(rf"(?<!\w){word}(?!\w)", normalized(self.previous.response)) and not re.search(
                rf"(?<!\w){word}(?!\w)", normalized(self.previous.prompt)
            ):
                results.append(ConversationSignal("jargon_follow_up", "term_from_previous_agent_reply", position - 1))

        self.user_measures.update(prompt_measures)
        self.agent_measures.update(reply_measures)
        self.user_values.update(prompt_values)
        self.agent_values.update(reply_values)
        self.previous = trace
        return results
