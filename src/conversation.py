import re
import unicodedata
from dataclasses import dataclass

from .models import Trace


INTENT_RULES = (
    ("feedback", r"^(?:מעולה|תודה(?: רבה)?|זה עזר|זה לא עזר|לא ענית|לא קיבלתי תשובה)(?:$|[\s,.!])"),
    ("access_and_identity", r"\b(?:אני .+מחובר|של מי|זה של .+ או |פה לידי|לידי והוא|ומה לגבי|ה?סיסמ[התו]|וידאו.+מאשר|אישור.+גישה)"),
    ("retrieve_or_locate", r"\b(?:איפה .*(?:רואה|מוצא|נמצא)|היכן .*(?:רואה|מוצא|נמצא)|שלוף|תשלוף|הצג|תציג|תראה לי)\b"),
    ("next_action", r"\b(?:מה (?:ניתן|אפשר|כדאי) לעשות|האם כדאי לי לבדוק|דרך להשפיע|איך (?:לשפר|להוריד|להפחית))\b"),
    ("clinical_question", r"\b(?:האם יש (?:לי |עדות ל)|האם אני סובל|יש לי תסמינים)"),
    ("compare_or_benchmark", r"\b(?:ביחס למשתתפים|ביחס לאחרים|בהשוואה לאחרים|כמה זה נורמלי|אחוזון)\b"),
    ("interpret_measurement", r"\b(?:מה המשמעות|מה אומר|מה זה אומר|האם זה תקין|הסבר נתון|הסבר כל ערך|לפי דוח|איך ה?\s*bmi)\b"),
    ("general_explanation", r"\b(?:איך (?:צוברים|נוצר|נוצרת)|מה משפיע|מה גורם)\b"),
    ("define_term", r"^(?:מה זה|מהו|מהי)\s+[^\s?]+\??$"),
)

TOPIC_RULES = (
    ("sleep", ("שינה", "דום נשימה", "נדודי שינה", "יקיצות", "sleep")),
    ("blood_tests", ("בדיקות דם", "בדיקת דם", "בדיקות הדם", "כולסטרול", "nightingale",
                     "גלוקוז", "סוכר בדם", "סוכר בצום", "המוגלובין", "טריגליצרידים", "hba1c")),
    ("body_composition", (
        "bmi", "הרכב גוף", "שומן ויסצרלי", "שומן וויסצרלי",
        "שומן הויסצרלי", "שומן הוויסצרלי", "משקל", "dxa",
    )),
    ("retinal_imaging", ("רשתית", "עורקים בעין", "רוחב העורקים", "קרקעית העין", "מקולה", "retina")),
    ("grip_strength", ("כוח אחיזה", "כוח האחיזה", "grip strength")),
    ("bone_density", ("צפיפות עצם", "צפיפות העצם", "bone density")),
    ("nutrition", ("תזונה", "תפריט תזונתי", "קלוריות", "צריכת חלבון", "ויטמינים", "ויטמין", "תוסף תזונה")),
    ("cardiovascular", ("לחץ דם", "לחץ הדם", "דופק", "קצב הלב")),
)
PAIN_WORDING = re.compile(r"\b[ובלמהשכ]?(?:כאב|כאבים|כאבי|הכאב|הכאבים|כואב|כואבת)\b")
HEALTH_WORDING = re.compile(r"\b(?:ה?בריאות(?:י|ית)?|ה?תסמינים|בדיקה רפואית|בדיקות רפואיות)\b")
TOPIC_KEYS = (*(label for label, _ in TOPIC_RULES), "pain", "general_health", "unclassified")
CHITCHAT = re.compile(
    r"^(?:היי|הי|שלום(?: רב)?|בוקר טוב|ערב טוב|לילה טוב|מה נשמע|יום טוב|להתראות|היוש)[\s!.?🙂👋]*$"
)
REQUEST_AFTER_ACK = re.compile(r"^(?:אבל\s+)?(?:מה|איך|האם|איפה|היכן|אפשר|תסביר(?:י)?|שלוף)\b")

# Restrict inheritance to common backward references. A new term or a named
# topic takes precedence over conversational proximity.
TOPIC_REFERENCE = re.compile(
    r"(?:\b(?:כל הפאנל|כל (?:ערך|מדד)|נתון נתון|המדדים האלה|הערכים האלה|הערכים הגבוהים|"
    r"השינוי הזה|התוצאה הזאת|הנתון הזה|הדוח הזה)\b"
    r"|^(?:ו?מה זה אומר|האם זה (?:תקין|נורמלי)|אפשר לפרט|זה (?:מדאיג|מטריד) אותי)[\s?.!]*$)"
)


def normalized(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def classify_intent(prompt: str) -> str:
    for label, pattern in INTENT_RULES:
        match = re.search(pattern, prompt)
        if match:
            if label == "feedback" and REQUEST_AFTER_ACK.match(prompt[match.end():].lstrip(" ,.!?")):
                continue
            return label
    return "chitchat" if CHITCHAT.fullmatch(prompt) else "unknown"


def topics_in(prompt: str) -> tuple[str, ...]:
    labels = [label for label, phrases in TOPIC_RULES if any(phrase in prompt for phrase in phrases)]
    if PAIN_WORDING.search(prompt):
        labels.append("pain")
    if not labels and HEALTH_WORDING.search(prompt):
        labels.append("general_health")
    return tuple(labels)


def topic_exclusion_reason(prompt: str, intent: str) -> str | None:
    if not prompt:
        return "empty_prompt"
    if intent in {"chitchat", "feedback"}:
        return intent
    if intent == "access_and_identity" and not (prompt.startswith("ומה לגבי") and topics_in(prompt)):
        return "access_and_identity"
    return None


@dataclass(frozen=True, slots=True)
class TopicEvidence:
    labels: tuple[str, ...] = ()
    anchor_position: int | None = None
    method: str = "direct"


def topic_evidence(
    trace: Trace, position: int, previous: Trace | None, prior_topic: TopicEvidence,
    semantic_topic: str | None = None,
) -> TopicEvidence:
    prompt = normalized(trace.prompt)
    direct = topics_in(prompt)
    if direct:
        return TopicEvidence(direct, position)
    intent = classify_intent(prompt)
    if intent in {"define_term", "feedback", "access_and_identity"}:
        return TopicEvidence()
    if not TOPIC_REFERENCE.search(prompt):
        return TopicEvidence((semantic_topic,), position, "semantic") if semantic_topic else TopicEvidence()
    if previous is None or len(prior_topic.labels) != 1:
        return TopicEvidence()
    if (not trace.account_id or not trace.session_id or trace.account_id != previous.account_id
            or trace.session_id != previous.session_id):
        return TopicEvidence()
    if trace.turn_number is not None and previous.turn_number is not None:
        adjacent = trace.turn_number == previous.turn_number + 1
    else:
        adjacent = bool(trace.timestamp and previous.timestamp and trace.timestamp > previous.timestamp)
    return prior_topic if adjacent else TopicEvidence()


@dataclass(frozen=True, slots=True)
class TurnReview:
    intent: str
    state_signals: tuple[str, ...]
    delivery: str
    next_user_action: str
    satisfaction: str
    satisfaction_scope: str
    evidence: tuple[str, ...]
    intent_source: str


def state_signals(prompt: str, intent: str) -> tuple[str, ...]:
    signals: list[str] = []
    for clause in re.split(r"(?<=[.!?])\s+", prompt):
        if "?" in clause or re.search(r"\b(?:האם|אם|יש לי\s+שאלה)\b", clause):
            continue
        if re.match(
            r"^(?:(?:הלילה |אתמול )?(?:קמתי|התעוררתי|ישנתי|נרדמתי)|יש לי|אני (?:מרגיש|מרגישה|סובל|סובלת)|"
            r"שמתי לב|מדדתי|(?:בבדיקה|בדוח) שלי)\b",
            clause,
        ):
            signals.append("self_report")
            break
    if re.search(
        r"(?:^|[.!?]\s+)(?:אני (?:מודאגת?|חוששת?|מוטרד(?:ת)?|מפחד(?:ת)?)|זה (?:מדאיג|מטריד) אותי)\b",
        prompt,
    ):
        signals.append("expressed_concern")
    third_person_bmi = re.search(r"(?:^|\s)ה?bmi\s+של\s+\S+", prompt)
    panel_explanation = re.search(r"\b(?:הפאנל|פאנל)\b.{0,50}\bותסביר(?:י)?\b", prompt)
    if panel_explanation or (intent in {"interpret_measurement", "compare_or_benchmark"} and not third_person_bmi):
        signals.append("measurement_question")
    if intent == "clinical_question":
        signals.append("clinical_concern")
    if intent == "next_action":
        signals.append("action_goal")
    return tuple(signals)


def assess_delivery(trace: Trace, prompt: str, reply: str, intent: str) -> tuple[str, str]:
    if trace.status == "error" and not reply:
        return "open", "error_without_reply"
    if not reply:
        return "unknown", "no_nonblank_reply"
    if re.search(r"\b(?:לא הצלחתי|אינו נגיש|לא נמצא בידי|לא ניתן לשלוף)\b", reply):
        if re.search(r"\d.*(?:גרם|ביקור)|(?:לאורך הביקורים|בין ביקור).+\d", reply):
            return "partial", "reported_unavailability_and_alternative_data"
        return "open", "reported_unavailability"
    if re.search(r"\b(?:לא יכול|ש?אינני יכול|ש?איני יכול|אין לי גישה|אין לי אפשרות|לא אפשרי)\b", reply):
        if intent in {"interpret_measurement", "clinical_question"} and re.search(r"\d", reply):
            return "partial", "boundary_with_measurements"
        return "bounded", "stated_boundary"
    if intent == "define_term" and re.search(r"\b(?:האם .+מתכוון|תוכל לפרט|תוכלי לפרט)\b", reply):
        return "open", "clarification_requested"
    if intent == "retrieve_or_locate" and re.search(r"\b(?:אוכל להציג|בשיחה הזו|רוצה שאשלוף)\b", reply):
        return "partial", "offered_access_route"
    if intent == "feedback":
        return "not_applicable", "feedback_turn"
    if intent in {"interpret_measurement", "general_explanation", "compare_or_benchmark", "next_action"}:
        shared_topics = set(topics_in(prompt)).intersection(topics_in(reply))
        if shared_topics or (intent == "compare_or_benchmark" and "אחוזון" in prompt and "אחוזון" in reply):
            return "addressed", "matching_topic_in_reply"
        if re.search(r"(?:ערכים גבוהים|הערכים הגבוהים|בין הבדיקות)", prompt) and re.search(r"\d", reply):
            return "addressed", "measurement_detail_in_reply"
    return "unknown", "coverage_unclear"


def follow_up(
    trace: Trace, prompt: str, intent: str, delivery: str, following: Trace | None,
) -> tuple[str, str, str, str]:
    if following is None:
        return "none_observed", "unknown", "unknown", "no_same_session_follow_up"
    next_prompt = normalized(following.prompt)
    if not next_prompt:
        return "unknown", "unknown", "unknown", "next_prompt_unavailable"
    if (trace.turn_number is not None and following.turn_number is not None
            and following.turn_number != trace.turn_number + 1):
        return "unknown", "unknown", "unknown", "nonconsecutive_recorded_turns"
    if next_prompt == prompt:
        return "repeated", "unknown", "unknown", "same_normalized_prompt"
    if re.search(r"^(?:מעולה|תודה|מצוין|כל הכבוד)[\s,.!]+בדקתי אותך[.!]?$", next_prompt):
        return "explicit_feedback", "positive", "interaction", "explicit_positive_test_feedback"
    if re.search(r"^(?:זה עזר(?: לי)?|תשובה מצוינת)[!.]*$", next_prompt):
        return "explicit_feedback", "positive", "previous_reply", "explicit_positive_reply_feedback"
    if re.search(r"^(?:לא ענית|זה לא עזר|לא קיבלתי תשובה)(?:$|[\s,.!?])", next_prompt):
        return "explicit_feedback", "negative", "previous_reply", "explicit_negative_reply_feedback"
    next_intent = classify_intent(next_prompt)
    if (delivery == "open" and intent == "retrieve_or_locate"
            and next_intent == "interpret_measurement" and re.search(r"נתון נתון|כל ערך", next_prompt)):
        return "refined", "unknown", "unknown", "request_narrowed_after_open_reply"
    if delivery == "partial" and intent == next_intent == "retrieve_or_locate":
        return "refined", "unknown", "unknown", "retrieval_follow_up"
    if intent == next_intent and intent not in {"unknown", "feedback", "access_and_identity"}:
        first, second = set(re.findall(r"[^\W_]+", prompt)), set(re.findall(r"[^\W_]+", next_prompt))
        shared_words = len(first & second)
        first_topics, second_topics = set(topics_in(prompt)), set(topics_in(next_prompt))
        if (shared_words >= 4 and shared_words / len(first | second) >= 0.6
                and (not first_topics or not second_topics or first_topics & second_topics)):
            return "similar_reformulation", "unknown", "unknown", "similar_prompt_tokens_same_intent"
    if next_intent == "access_and_identity" and re.search(r"^אני .+מחובר", next_prompt):
        return "clarified", "unknown", "unknown", "identity_context_added"
    if next_intent != intent and {intent, next_intent}.isdisjoint({"unknown", "feedback"}):
        return "new_request", "unknown", "unknown", "different_detected_intent"
    return "continued", "unknown", "unknown", "next_turn_without_explicit_feedback"


def review_turn(trace: Trace, following: Trace | None, semantic_intent: str | None = None) -> TurnReview:
    prompt, reply = normalized(trace.prompt), normalized(trace.response)
    intent = classify_intent(prompt)
    source = "rule" if intent != "unknown" else "unknown"
    if intent == "unknown" and semantic_intent:
        intent, source = semantic_intent, "semantic"
    delivery, delivery_evidence = assess_delivery(trace, prompt, reply, intent)
    action, satisfaction, scope, reaction_evidence = follow_up(trace, prompt, intent, delivery, following)
    return TurnReview(
        intent, state_signals(prompt, intent), delivery, action, satisfaction, scope,
        (delivery_evidence, reaction_evidence), source,
    )
