from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal

from .conversation import TOPIC_KEYS, TopicEvidence, normalized, review_turn, topic_evidence, topic_exclusion_reason
from .conversation_signals import SessionSignals, adjacent
from .metrics import (
    DOCTOR_REFERRAL, average, clinical_signals, cost_and_latency, friction_metrics, longest_error_streak,
    rate, session_lengths, time_distribution, user_dynamics,
)
from .models import Trace, TraceBatch
from .request_coverage import review_components
from .semantic import CandidateTurn, Encoder, discover, suggestions
from .session_links import RequestTurn, index_request, links_for_user, subjects_in
from .state_findings import FindingEvidence, FindingKey, collect_findings, summarize_findings
from .understanding import UnderstandingContext


Source = Literal["local_json", "langfuse_traces"]


def summarize(batch: TraceBatch) -> dict[str, object]:
    traces = batch.traces
    return {
        "schema_version": "1.0",
        "raw_row_count": batch.raw_row_count,
        "trace_count": len(traces),
        "session_count": len({t.session_id for t in traces if t.session_id is not None}),
        "user_count": len({t.account_id for t in traces if t.account_id is not None}),
        "error_trace_count": sum(t.status == "error" for t in traces),
        "empty_response_count": sum(t.response is not None and not t.response.strip() for t in traces),
        "missing_response_count": sum(t.response is None for t in traces),
        "skipped_row_count": batch.skipped_row_count,
        "duplicate_row_count": batch.duplicate_row_count,
    }


def order_key(trace: Trace) -> tuple[bool, int, datetime, str]:
    return (
        trace.turn_number is None, trace.turn_number or 0,
        trace.timestamp or datetime.max.replace(tzinfo=UTC), trace.trace_id,
    )


def grouped_traces(batch: TraceBatch) -> list[list[list[Trace]]]:
    users: dict[tuple[str, str], dict[tuple[str, str], list[Trace]]] = {}
    for trace in batch.traces:
        # Unknown owners and sessions are kept apart to avoid false feedback attribution.
        user = ("account", trace.account_id) if trace.account_id else ("row", trace.trace_id)
        session = ("session", trace.session_id) if trace.session_id else ("row", trace.trace_id)
        users.setdefault(user, {}).setdefault(session, []).append(trace)
    ranked = sorted(users.values(), key=lambda sessions: -max(map(len, sessions.values())))
    return [
        [sorted(traces, key=order_key) for traces in sorted(sessions.values(), key=lambda group: -len(group))]
        for sessions in ranked
    ]


def analyze(
    batch: TraceBatch, source: Source, *, include_review: bool = False, encoder: Encoder | None = None,
) -> dict[str, object]:
    topic_rows: dict[str, list[Trace]] = defaultdict(list)
    topic_context_counts: Counter[str] = Counter()
    topic_model_counts: Counter[str] = Counter()
    topic_exclusions: Counter[str] = Counter()
    friction_followups: Counter[str] = Counter()
    inferred = suggestions(batch.traces, encoder) if encoder is not None else {}
    unknown_topics: list[CandidateTurn] = []
    breakdowns: dict[str, Counter[str]] = {
        name: Counter() for name in ("intent", "delivery", "next_user_action", "satisfaction", "state_signals")
    }
    intent_users: dict[str, set[str]] = defaultdict(set)
    intent_sessions: dict[str, set[str]] = defaultdict(set)
    review_rows: list[dict[str, object]] = []
    user_summaries: list[dict[str, object]] = []
    state_user_counts: Counter[str] = Counter()
    finding_users: Counter[str] = Counter()
    signal_users: Counter[str] = Counter()
    signal_counts: dict[str, Counter[str]] = defaultdict(Counter)
    component_statuses: Counter[str] = Counter()
    component_kinds: dict[str, Counter[str]] = defaultdict(Counter)
    turns_with_components = 0
    understanding_count = 0
    understanding_intents: Counter[str] = Counter()
    understanding_domains: Counter[str] = Counter()
    understanding_sources: Counter[str] = Counter()
    session_links: list[dict[str, object]] = []
    users = grouped_traces(batch)
    for user_number, sessions in enumerate(users, 1):
        request_index: dict[str, list[RequestTurn]] = defaultdict(list)
        state_groups: dict[tuple[str, str | None], list[dict[str, int | str | None]]] = defaultdict(list)
        finding_groups: dict[FindingKey, list[FindingEvidence]] = {}
        user_intents: Counter[str] = Counter()
        user_topics: Counter[str] = Counter()
        user_conversation_signals: Counter[str] = Counter()
        known_user = sessions[0][0].account_id is not None
        for session_number, traces in enumerate(sessions, 1):
            previous = None
            topic = TopicEvidence()
            subjects_by_position: dict[int, tuple[str, ...]] = {}
            signal_context = SessionSignals()
            understanding_context = UnderstandingContext()
            for position, trace in enumerate(traces, 1):
                next_trace = traces[position] if position < len(traces) else None
                suggestion = inferred.get(trace.trace_id)
                prompt = normalized(trace.prompt)
                review = review_turn(trace, next_trace, suggestion.intent if suggestion else None)
                understanding = understanding_context.inspect(trace, review.intent, position)
                if understanding is not None:
                    understanding_count += 1
                    understanding_intents.update(understanding.understanding_intent or ("unresolved",))
                    understanding_domains[understanding.test_target.domain or "unresolved"] += 1
                    understanding_sources[understanding.resolution_source] += 1
                signals = signal_context.inspect(trace, next_trace, position)
                for signal in signals:
                    signal_counts[signal.kind][signal.observation] += 1
                    user_conversation_signals[signal.kind] += 1
                components = review_components(trace, review.evidence[0])
                turns_with_components += bool(components)
                for component in components:
                    component_statuses[component.status] += 1
                    component_kinds[component.kind][component.status] += 1
                if adjacent(trace, next_trace) and next_trace is not None and normalized(next_trace.prompt):
                    if review.delivery in {"open", "partial"} or any(
                        component.status in {"reported_blocker", "omitted_without_explanation", "execution_error"}
                        for component in components
                    ):
                        friction_followups["incomplete_with_follow_up"] += 1
                        friction_followups["repeat_after_incomplete"] += review.next_user_action in {"repeated", "similar_reformulation"}
                    if DOCTOR_REFERRAL.search(normalized(trace.response)):
                        friction_followups["referral_with_follow_up"] += 1
                        friction_followups["referral_pushback"] += any(
                            signal.kind == "disclaimer_follow_up" and signal.observation == "explicit_pushback"
                            for signal in signals
                        )
                exclusion = topic_exclusion_reason(prompt, review.intent)
                topic = TopicEvidence() if exclusion else topic_evidence(
                    trace, position, previous, topic, suggestion.topic if suggestion else None,
                )
                previous = trace
                topic_source = "unknown"
                if topic.labels:
                    topic_source = topic.method if topic.anchor_position == position else "previous_user_turn"
                elif not exclusion and suggestion and trace.account_id and trace.session_id:
                    unknown_topics.append(CandidateTurn(
                        trace.account_id, user_number, session_number, position, suggestion.vector,
                    ))
                if exclusion:
                    topic_exclusions[exclusion] += 1
                for label in (() if exclusion else topic.labels or ("unclassified",)):
                    topic_rows[label].append(trace)
                    user_topics[label] += 1
                    if topic_source == "previous_user_turn":
                        topic_context_counts[label] += 1
                    elif topic_source == "semantic":
                        topic_model_counts[label] += 1
                user_intents[review.intent] += 1
                state_topic = topic.labels[0] if len(topic.labels) == 1 else None
                if known_user:
                    subjects_by_position[position] = subjects_in(prompt)
                    collect_findings(
                        finding_groups, trace, review, components, session_number, position, topic, subjects_by_position,
                    )
                    for signal in review.state_signals:
                        state_groups[signal, state_topic].append({
                            "session_number": session_number,
                            "position": position,
                            "recorded_turn": trace.turn_number,
                            "topic_source": topic_source if state_topic is not None else "unknown",
                            "topic_anchor_position": topic.anchor_position if state_topic is not None else None,
                        })
                index_request(request_index, trace, review, session_number, position)
                breakdowns["intent"][review.intent] += 1
                breakdowns["delivery"][review.delivery] += 1
                breakdowns["next_user_action"][review.next_user_action] += 1
                breakdowns["satisfaction"][review.satisfaction] += 1
                breakdowns["state_signals"].update(review.state_signals)
                if trace.account_id is not None:
                    intent_users[review.intent].add(trace.account_id)
                if trace.session_id is not None:
                    intent_sessions[review.intent].add(trace.session_id)
                if include_review:
                    review_rows.append({
                        "user_number": user_number, "session_number": session_number,
                        "position": position, "recorded_turn": trace.turn_number,
                        "next_position": position + 1 if next_trace is not None else None,
                        "execution_status": trace.status,
                        "topics": topic.labels,
                        "topic_source": topic_source,
                        "topic_exclusion_reason": exclusion,
                        "topic_anchor_position": topic.anchor_position,
                        "topic_anchor_method": topic.method if topic.labels else None,
                        "request_components": [asdict(component) for component in components],
                        "conversation_signals": [asdict(signal) for signal in signals],
                        "understanding_request": asdict(understanding) if understanding else None,
                        **asdict(review),
                    })
        user_links = links_for_user(user_number, request_index)
        session_links.extend(user_links)
        if known_user:
            state_user_counts.update({signal for signal, _ in state_groups})
            finding_users.update({key.focus for key in finding_groups})
            signal_users.update(user_conversation_signals.keys())
            if include_review:
                known_sessions = [group for group in sessions if group[0].session_id is not None]
                lengths = session_lengths(known_sessions)
                user_summaries.append({
                    "user_number": user_number,
                    "trace_count": sum(user_intents.values()),
                    "session_count": len(known_sessions),
                    "intents": dict(sorted(user_intents.items())),
                    "topics": dict(sorted(user_topics.items())),
                    "single_observed_turn": rate(lengths["single_observed_turn"], len(known_sessions)),
                    "single_observed_at_turn_1": rate(lengths["single_observed_at_turn_1"], len(known_sessions)),
                    "error_trace_count": sum(t.status == "error" for group in sessions for t in group),
                    "blank_reply_count": sum(t.response is not None and not t.response.strip() for group in sessions for t in group),
                    "longest_observed_error_streak": longest_error_streak(known_sessions),
                    "recurring_topics": recurring_topics(user_links),
                    "state_findings": summarize_findings(finding_groups),
                    "conversation_signal_counts": dict(sorted(user_conversation_signals.items())),
                    "state": [
                        {"signal": signal, "topic": label, "trace_count": len(positions), "evidence": positions}
                        for (signal, label), positions in state_groups.items()
                    ],
                })

    result: dict[str, object] = {
        "schema_version": "2.11",
        "source": source,
        "summary": summarize(batch),
        "topics": [
            {
                "topic": topic,
                "trace_count": len(topic_rows[topic]),
                "user_count": len({t.account_id for t in topic_rows[topic] if t.account_id is not None}),
                "contextual_trace_count": topic_context_counts[topic],
                "semantic_trace_count": topic_model_counts[topic],
            }
            for topic in TOPIC_KEYS
        ],
        "topic_population": {
            "eligible_trace_count": len(batch.traces) - topic_exclusions.total(),
            "excluded_trace_count": topic_exclusions.total(),
            "excluded_by_reason": dict(sorted(topic_exclusions.items())),
            "unclassified_trace_count": len(topic_rows["unclassified"]),
            "semantic_trace_count": topic_model_counts.total(),
        },
        "trace_cost_usd": average([t.cost_usd for t in batch.traces if t.cost_usd is not None]),
        "trace_latency_seconds": average([t.latency_seconds for t in batch.traces if t.latency_seconds is not None]),
        "latency_scope": "langfuse_trace" if source == "langfuse_traces" else "exported_trace",
        "user_dynamics": user_dynamics(users),
        "time_distribution": time_distribution(batch.traces, topic_rows),
        "clinical_signals": clinical_signals(batch.traces, topic_rows),
        "friction_metrics": {
            **friction_metrics(batch.traces, users, breakdowns["next_user_action"]),
            "error_rate": rate(sum(t.status == "error" for t in batch.traces), len(batch.traces)),
            "repeat_after_incomplete": rate(
                friction_followups["repeat_after_incomplete"], friction_followups["incomplete_with_follow_up"],
            ),
            "doctor_referral_followup_pushback": rate(
                friction_followups["referral_pushback"], friction_followups["referral_with_follow_up"],
            ),
        },
        "cost_and_latency": cost_and_latency(batch.traces, topic_rows),
        "conversation": {name: dict(sorted(counts.items())) for name, counts in breakdowns.items()},
        "state_users": dict(sorted(state_user_counts.items())),
        "state_finding_users_by_focus": dict(sorted(finding_users.items())),
        "understanding_requests": {
            "trace_count": understanding_count,
            "by_intent": dict(sorted(understanding_intents.items())),
            "by_domain": dict(sorted(understanding_domains.items())),
            "by_resolution_source": dict(sorted(understanding_sources.items())),
        },
        "conversation_signals": {
            kind: {
                "trace_count": observations.total(),
                "known_user_count": signal_users[kind],
                "observations": dict(sorted(observations.items())),
            }
            for kind, observations in sorted(signal_counts.items())
        },
        "request_coverage": {
            "turns_with_components": turns_with_components,
            "component_count": component_statuses.total(),
            "by_status": dict(sorted(component_statuses.items())),
            "by_component": {kind: dict(sorted(counts.items())) for kind, counts in sorted(component_kinds.items())},
        },
        "intents_by_reach": [
            {
                "intent": intent, "trace_count": count,
                "session_count": len(intent_sessions[intent]), "user_count": len(intent_users[intent]),
            }
            for intent, count in sorted(breakdowns["intent"].items())
        ],
        "cross_session": {
            "link_count": len(session_links),
            "user_count": len({link["user_number"] for link in session_links}),
            "users_with_repeated_request": len({
                link["user_number"] for link in session_links if link["kind"] == "repeated_request"
            }),
            "links_by_kind": dict(sorted(Counter(link["kind"] for link in session_links).items())),
        },
    }
    if include_review:
        result["review"] = review_rows
        result["user_summaries"] = user_summaries
        result["cross_session_links"] = session_links
    if encoder is not None:
        result["topic_discovery"] = discover(unknown_topics, include_review=include_review)
    return result


def recurring_topics(links: list[dict[str, object]]) -> list[dict[str, object]]:
    by_subject: dict[object, list[dict[str, object]]] = defaultdict(list)
    for link in links:
        by_subject[link["subject"]].append({key: value for key, value in link.items() if key not in {"user_number", "subject"}})
    return [{"subject": subject, "links": matching} for subject, matching in by_subject.items()]
