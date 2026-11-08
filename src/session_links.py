import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from typing import Literal

from .conversation import TurnReview, normalized
from .models import Trace


# Specific measures or artifacts. Broad categories such as body composition
# would also connect unrelated questions about BMI and visceral fat.
SUBJECT_RULES = (
    ("artery_width", r"רוחב (?:ה?עורקים|עורקי (?:העין|הרשתית))"),
    ("visceral_fat", r"שומן ה?ו(?:ו)?יסצרלי"),
    ("bmi", r"\bbmi\b"),
    ("nightingale_panel", r"\bnightingale\b"),
    ("grip_strength", r"כוח (?:ה)?אחיזה|\bgrip strength\b"),
    ("bone_density", r"צפיפות (?:ה)?עצם|\bbone density\b"),
    ("sleep_apnea", r"דום נשימה|\bpahi\b|\bsleep apnea\b"),
    ("sleep_quality", r"איכות (?:ה)?שינה|שינה איכותית|\bsleep quality\b"),
    ("cholesterol", r"כולסטרול|\bcholesterol\b"),
)
LinkKind = Literal["repeated_request", "same_intent_and_subject", "shared_subject"]


def subjects_in(text: str) -> tuple[str, ...]:
    return tuple(subject for subject, pattern in SUBJECT_RULES if re.search(pattern, text))


@dataclass(frozen=True, slots=True)
class RequestTurn:
    session_number: int
    position: int
    recorded_turn: int | None
    timestamp: datetime | None
    intent: str
    delivery: str
    tokens: frozenset[str]


def index_request(
    index: dict[str, list[RequestTurn]],
    trace: Trace, review: TurnReview, session_number: int, position: int,
) -> None:
    if trace.account_id is None or trace.session_id is None or review.intent in {"feedback", "access_and_identity"}:
        return
    prompt = normalized(trace.prompt)
    subjects = subjects_in(prompt)
    if subjects:
        request = RequestTurn(
            session_number, position, trace.turn_number, trace.timestamp,
            review.intent, review.delivery, frozenset(re.findall(r"[^\W_]+", prompt)),
        )
        for subject in subjects:
            index[subject].append(request)


def classify_pair(first: RequestTurn, second: RequestTurn) -> tuple[LinkKind, float]:
    if first.intent != second.intent or first.intent == "unknown":
        return "shared_subject", 0.0
    overlap = len(first.tokens & second.tokens) / len(first.tokens | second.tokens)
    if overlap >= 0.6:
        return "repeated_request", overlap
    return "same_intent_and_subject", overlap


def reference(turn: RequestTurn) -> dict[str, int | str | None]:
    return {
        "session_number": turn.session_number, "position": turn.position,
        "recorded_turn": turn.recorded_turn, "intent": turn.intent, "delivery": turn.delivery,
    }


def describe_link(
    user_number: int, subject: str, kind: LinkKind, first: RequestTurn, second: RequestTurn,
) -> dict[str, object]:
    ordered = False
    if first.timestamp is not None and second.timestamp is not None:
        ordered = first.timestamp != second.timestamp
        if first.timestamp > second.timestamp:
            first, second = second, first

    follow_through = "unknown"
    # Topic continuity alone cannot show that the original request got an answer.
    if ordered and kind == "repeated_request" and first.delivery in {"open", "partial"}:
        if second.delivery == "addressed":
            follow_through = "later_reply_addresses_topic"
        elif second.delivery in {"open", "partial"}:
            follow_through = "still_open_or_partial"

    evidence = ["same_account", "different_sessions", "same_subject_in_user_prompts"]
    if kind != "shared_subject":
        evidence.append("same_detected_intent")
    if kind == "repeated_request":
        evidence.append("similar_prompt_tokens")
    return {
        "user_number": user_number, "subject": subject, "kind": kind,
        "order": "chronological" if ordered else "unknown",
        "turns": [reference(first), reference(second)],
        "follow_through": follow_through, "evidence": evidence,
    }


def links_for_user(user_number: int, index: dict[str, list[RequestTurn]]) -> list[dict[str, object]]:
    links = []
    priority = {"shared_subject": 0, "same_intent_and_subject": 1, "repeated_request": 2}
    for subject, requests in index.items():
        sessions: dict[int, list[RequestTurn]] = defaultdict(list)
        for request in requests:
            sessions[request.session_number].append(request)
        for left, right in combinations(sessions.values(), 2):
            best_score = (-1, -1.0)
            best: tuple[LinkKind, RequestTurn, RequestTurn] | None = None
            for first in left:
                for second in right:
                    kind, overlap = classify_pair(first, second)
                    score = (priority[kind], overlap)
                    if score > best_score:
                        best_score = score
                        best = kind, first, second
            if best is not None:
                links.append(describe_link(user_number, subject, *best))
    return links
