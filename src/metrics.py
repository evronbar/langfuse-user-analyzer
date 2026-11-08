import re
from collections import Counter, defaultdict
from decimal import Decimal, localcontext
from math import ceil
from statistics import median

from .conversation import normalized
from .models import Trace


NUMERIC_MEASUREMENT = re.compile(
    r"\d(?:[.,]\d+)?\s*(?:%|mg/dl\b|mmol/l\b|bpm\b|ק[״\"]?ג\b|גרם\b|שעות\b|דקות\b)"
    r"|\b(?:bmi|hba1c|אחוזון|לחץ דם)\s*(?::|של|הוא)?\s*\d"
)
CONCERN_WORDING = re.compile(
    r"\b(?:אני (?:מודאגת?|חוששת?|מוטרד(?:ת)?)|(?:מדאיג|מטריד|מפחיד) אותי|"
    r"הרופא הלחיץ אותי|האם זה (?:מסוכן|חריג))\b"
)
URGENCY_WORDING = re.compile(r"\b(?:דחוף|מיידית|מיד עכשיו)\b")
DOCTOR_REFERRAL = re.compile(
    r"\b(?:(?:לפנות|פנה|פני|התייעץ|התייעצי|להתייעץ)\s+(?:עם\s+)?(?:ל?רופא(?:ה)?|ל?רופא(?:ה)? המטפל(?:ת)?)"
    r"|לשוחח עם (?:ה)?רופא(?:ה)?)\b"
)


def average(values: list[Decimal]) -> dict[str, object]:
    if not values:
        return {"count": 0, "mean": None}
    with localcontext() as context:
        context.prec = max(28, max(value.adjusted() for value in values) + 16)
        mean = format(sum(values, Decimal(0)) / len(values), ".6f")
    return {"count": len(values), "mean": mean}


def rate(count: int, total: int) -> dict[str, int | float | None]:
    return {"count": count, "eligible_count": total, "ratio": round(count / total, 6) if total else None}


def distribution(values: list[int | float]) -> dict[str, int | float | None]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "mean": round(sum(ordered) / len(ordered), 6) if ordered else None,
        "median": median(ordered) if ordered else None,
        "p90": ordered[ceil(len(ordered) * 0.9) - 1] if ordered else None,
    }


def session_lengths(sessions: list[list[Trace]]) -> dict[str, int]:
    lengths = {"single_observed_turn": 0, "multiple_observed_turns": 0, "single_observed_at_turn_1": 0}
    for session in sessions:
        if len(session) > 1:
            lengths["multiple_observed_turns"] += 1
        else:
            lengths["single_observed_turn"] += 1
            lengths["single_observed_at_turn_1"] += session[0].turn_number == 1
    return lengths


def inter_session_hours(sessions: list[list[Trace]]) -> list[float]:
    if any(trace.timestamp is None for session in sessions for trace in session):
        return []
    times = [[t.timestamp for t in session if t.timestamp is not None] for session in sessions]
    spans = sorted((min(timestamps), max(timestamps)) for timestamps in times)
    gaps = []
    latest_end = None
    for start, end in spans:
        if latest_end is not None and start >= latest_end:
            gaps.append(round((start - latest_end).total_seconds() / 3600, 6))
        latest_end = max(latest_end, end) if latest_end is not None else end
    return gaps


def longest_error_streak(sessions: list[list[Trace]]) -> int:
    longest = 0
    for session in sessions:
        streak = 0
        previous_turn = None
        for trace in session:
            current_turn = trace.turn_number
            if trace.status == "error":
                consecutive = previous_turn is not None and current_turn == previous_turn + 1
                streak = streak + 1 if consecutive else 1
                longest = max(longest, streak)
            else:
                streak = 0
            previous_turn = current_turn
    return longest


def user_dynamics(users: list[list[list[Trace]]]) -> dict[str, object]:
    traces_per_user = []
    sessions_per_user = []
    intervals = []
    cohorts: Counter[str] = Counter({"one_trace": 0, "multiple_traces_up_to_10_sessions": 0, "over_10_sessions": 0})
    total_sessions: Counter[str] = Counter()
    for group in users:
        if group[0][0].account_id is None:
            continue
        sessions = [session for session in group if session[0].session_id is not None]
        trace_count = sum(map(len, group))
        traces_per_user.append(trace_count)
        sessions_per_user.append(len(sessions))
        total_sessions.update(session_lengths(sessions))
        intervals.extend(inter_session_hours(sessions))
        if trace_count == 1:
            cohorts["one_trace"] += 1
        elif len(sessions) > 10:
            cohorts["over_10_sessions"] += 1
        else:
            cohorts["multiple_traces_up_to_10_sessions"] += 1
    session_count = sum(sessions_per_user)
    return {
        "traces_per_user": distribution(traces_per_user),
        "sessions_per_user": distribution(sessions_per_user),
        "single_observed_turn": rate(total_sessions["single_observed_turn"], session_count),
        "single_observed_at_turn_1": rate(total_sessions["single_observed_at_turn_1"], session_count),
        "inter_session_hours": distribution(intervals),
        "engagement_groups": dict(cohorts),
    }


def time_distribution(traces: tuple[Trace, ...], topic_rows: dict[str, list[Trace]]) -> dict[str, object]:
    hours = Counter(t.timestamp.hour for t in traces if t.timestamp is not None)
    topic_hours = {
        topic: Counter(t.timestamp.hour for t in rows if t.timestamp is not None)
        for topic, rows in sorted(topic_rows.items())
    }
    return {
        "timezone": "UTC",
        "traces_by_hour": [hours[hour] for hour in range(24)],
        "between_00_and_06": rate(sum(hours[hour] for hour in range(6)), hours.total()),
        "between_01_and_04": rate(sum(hours[hour] for hour in range(1, 4)), hours.total()),
        "missing_timestamp_count": len(traces) - hours.total(),
        "between_00_and_06_by_topic": {
            topic: rate(sum(counts[hour] for hour in range(6)), counts.total())
            for topic, counts in topic_hours.items()
        },
        "between_01_and_04_by_topic": {
            topic: rate(sum(counts[hour] for hour in range(1, 4)), counts.total())
            for topic, counts in topic_hours.items()
        },
    }


def clinical_signals(traces: tuple[Trace, ...], topic_rows: dict[str, list[Trace]]) -> dict[str, object]:
    prompts = [(trace, normalized(trace.prompt)) for trace in traces if normalized(trace.prompt)]
    concern = {trace.trace_id for trace, prompt in prompts if CONCERN_WORDING.search(prompt)}
    urgency = {trace.trace_id for trace, prompt in prompts if URGENCY_WORDING.search(prompt)}
    signals = concern | urgency
    return {
        "numeric_measurement_mention": rate(sum(bool(NUMERIC_MEASUREMENT.search(prompt)) for _, prompt in prompts), len(prompts)),
        "concern_wording": rate(len(concern), len(prompts)),
        "urgency_wording": rate(len(urgency), len(prompts)),
        "concern_or_urgency_wording": rate(len(signals), len(prompts)),
        "users_with_concern_or_urgency_wording": len({
            trace.account_id for trace, _ in prompts if trace.trace_id in signals and trace.account_id is not None
        }),
        "concern_or_urgency_traces_by_topic": {
            topic: sum(t.trace_id in signals for t in rows) for topic, rows in sorted(topic_rows.items())
        },
    }


def friction_metrics(
    traces: tuple[Trace, ...], users: list[list[list[Trace]]], next_actions: Counter[str],
) -> dict[str, object]:
    final_turns = Counter()
    for group in users:
        for session in group:
            if session[0].account_id is None or session[0].session_id is None:
                continue
            turns = [t.turn_number for t in session if t.turn_number is not None]
            final_turns[str(max(turns)) if len(turns) == len(session) else "unknown"] += 1
    replies = [normalized(trace.response) for trace in traces if normalized(trace.response)]
    errors = [t for t in traces if t.status == "error"]
    blank_replies = [t for t in traces if t.response is not None and not t.response.strip()]
    return {
        "last_observed_turn_counts": dict(sorted(final_turns.items())),
        "last_observed_turn_1": rate(final_turns["1"], final_turns.total() - final_turns["unknown"]),
        "exact_repeat_count": next_actions["repeated"],
        "similar_reformulation_count": next_actions["similar_reformulation"],
        "doctor_referral_wording": rate(sum(bool(DOCTOR_REFERRAL.search(reply)) for reply in replies), len(replies)),
        "error_trace_count": len(errors),
        "users_with_errors": len({t.account_id for t in errors if t.account_id is not None}),
        "blank_reply_count": len(blank_replies),
        "users_with_blank_replies": len({t.account_id for t in blank_replies if t.account_id is not None}),
    }


def cost_and_latency(
    traces: tuple[Trace, ...], topic_rows: dict[str, list[Trace]],
) -> dict[str, object]:
    user_costs: dict[str, Decimal] = defaultdict(Decimal)
    for trace in traces:
        if trace.account_id is not None and trace.cost_usd is not None:
            user_costs[trace.account_id] += trace.cost_usd
    ranked = sorted(user_costs.values(), reverse=True)
    top_count = ceil(len(ranked) / 10)
    total = sum(ranked, Decimal(0))
    return {
        "top_10_percent_user_cost": {
            "eligible_user_count": len(ranked),
            "top_user_count": top_count,
            "cost_share": float(round(sum(ranked[:top_count], Decimal(0)) / total, 6)) if total else None,
        },
        "by_topic": {
            topic: {
                "trace_cost_usd": average([t.cost_usd for t in rows if t.cost_usd is not None]),
                "latency_seconds": average([t.latency_seconds for t in rows if t.latency_seconds is not None]),
            }
            for topic, rows in sorted(topic_rows.items())
        },
    }
