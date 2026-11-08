# Architecture and contracts | Langfuse User Analyzer

```yaml
spec_version: 0.2.0
status: draft
interfaces: [cli, langfuse-traces, local-json]
milestone_order: [M1, M2, M3]
```

## D-01 | Component boundaries

```text
CLI → choose local JSON or Langfuse HTTP
    → load raw rows → normalize → aggregate → stdout
                                           → PDF if requested in M3
```

Each input adapter returns rows in the assignment's trace shape. The live adapter projects API traces into that shape. Normalization and aggregation are source-independent after projection. No raw row crosses the reporting boundary. The application exposes a CLI and has no inbound HTTP endpoint.

## D-02 | CLI and environment

```text
python -m src.main [--input-json PATH] [--summary-only | --review] [--semantic-model DIR]
python -m src.main [--input-json PATH] --output PATH.pdf # M3
```

| Setting | Validation |
|---|---|
| `PHA_LF_HOST` | Absolute HTTPS origin with hostname and optional port. Allow a trailing slash. Reject credentials, non-root paths, query, fragment, and redirects. |
| `PHA_LF_PUBLIC_KEY`, `PHA_LF_SECRET_KEY` | Nonempty strings without whitespace. Used only for HTTP Basic Auth. |
| `PHA_LF_FROM`, `PHA_LF_TO` | Full ISO 8601 date-time with `Z` or a numeric timezone offset. Require start < end. Normalize to UTC before sending. |
| `--input-json PATH` | Read UTF-8 JSON whose top-level value is an array. Ignore live configuration in this mode. Analyze all rows in the file. |
| `--semantic-model DIR` | Optional local E5 tokenizer and INT8 ONNX model directory. Requires analysis mode. The model adapter performs no HTTP calls. |
| `--output PATH.pdf` | M3 only. Require a `.pdf` suffix and a writable destination. Create the parent directory if necessary. Container analysis defaults to `/output/report.pdf` when the flag is omitted. |

Stdout carries one JSON object and a newline on success. Emit Summary with `--summary-only` and Analysis otherwise. Only `--review` adds per-turn labels. In M3, emit stdout only after the optional PDF succeeds. Stderr may contain a stable error category, a setting name, a page number, or an HTTP status code. It must not contain credential values, request URLs, input paths, bodies, or tracebacks.

## D-03 | Langfuse trace-list adapter

The assignment path is `GET /api/public/traces`. The configured host must still expose this legacy endpoint. The adapter returns a clear error if the endpoint is unavailable.

| HTTP contract | Value |
|---|---|
| Authentication | Basic Auth, public key as username and secret key as password. |
| Query | `fromTimestamp`, `toTimestamp`, `page`, and `limit=50`. Both timestamps are sent in UTC. |
| Pagination | Start at page 1. Validate integer `meta.page` and nonnegative integer `meta.totalPages`. Fetch through the last page, with a 1,000-page safety limit. |
| Transport | TLS verification, no redirects or implicit proxies. Connect timeout 5 s, read timeout 30 s. |
| Retry | Up to three attempts per page for timeout, connection failure other than TLS, HTTP 429, or HTTP 5xx. Respect integer `Retry-After` up to 30 s. |

The response must contain a `data` array and a `meta` object. Project each trace onto `id`, `timestamp`, `sessionId`, `input.user_prompt`, `output.response`, optional `output.status`, `metadata.account_uuid`, `metadata.status`, `metadata.turn_number`, `latency`, and `totalCost`. Accept nested JSON objects or strings that decode to objects. D-04 handles malformed individual rows and unavailable fields.

[Langfuse compatibility matrix](https://langfuse.com/self-hosting/upgrade/versioning)

## D-04 | Normalization and domain model

```python
# src/models.py | All data classes are frozen and use slots.
@dataclass(frozen=True, slots=True)
class LiveConfig:
    host: str
    public_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    start: datetime  # timezone-aware UTC
    end: datetime    # timezone-aware UTC

@dataclass(frozen=True, slots=True)
class Trace:
    trace_id: str
    account_id: str | None
    session_id: str | None
    turn_number: int | None
    timestamp: datetime | None
    prompt: str | None
    response: str | None
    status: Literal["completed", "error", "unknown"]
    latency_seconds: Decimal | None
    cost_usd: Decimal | None

@dataclass(frozen=True, slots=True)
class TraceBatch:
    traces: tuple[Trace, ...]
    raw_row_count: int
    skipped_row_count: int
    duplicate_row_count: int
```

| Source | Rule |
|---|---|
| Row and `id` | Skip non-object rows and rows without a nonblank string ID. Trim IDs. Keep the first eligible row per ID. Count subsequent rows with the same ID as duplicates even if their content differs. |
| `metadata.account_uuid`, `sessionId` | Trim nonblank strings. Otherwise use `None`. No fallback to `userId`. |
| `metadata.turn_number` | Positive JSON integer. Otherwise `None`. Booleans do not qualify. |
| `timestamp` | Same time syntax as D-02. Convert to UTC. Otherwise `None`. |
| `input.user_prompt`, `output.response` | Keep strings verbatim, including blank strings. Absent, non-string, or non-object parent becomes `None`. |
| `metadata.status`, `output.status` | Trim and case-fold. Prefer recognized metadata status, then recognized output status, otherwise `unknown`. |
| `latency`, `totalCost` | Finite nonnegative JSON number, excluding booleans. Convert to decimal. Invalid or absent becomes `None`. Latency is in seconds. Cost is in USD. |

Batch invariant: `raw_row_count == len(traces) + skipped_row_count + duplicate_row_count`. Preserve source order in the batch. If later analysis needs ordered conversations, partition by non-null `session_id` and sort by `(turn_number, timestamp, trace_id)` with absent turns and timestamps after present values. Do not group absent session IDs together.

## D-05 | Callable interfaces

```python
# src/main.py
def read_config(env: Mapping[str, str]) -> LiveConfig: ...
def main(argv: Sequence[str] | None = None) -> int: ...

# src/fetcher.py
def read_local(path: Path) -> tuple[object, ...]: ...
def fetch_live(config: LiveConfig) -> tuple[object, ...]: ...
def normalize_rows(rows: Sequence[object]) -> TraceBatch: ...

# src/analyzer.py
def summarize(batch: TraceBatch) -> dict[str, object]: ...  # M1
def analyze(batch: TraceBatch, source: Literal["local_json", "langfuse_traces"], *, include_review: bool = False) -> dict[str, object]: ...  # M2

# src/reporter.py
def write_pdf(analysis: Analysis, destination: Path) -> None: ...  # M3
```

The CLI emits dictionaries matching the JSON DTOs below. A success DTO contains only the listed keys. Introduce no API route, persisted cache, or raw export.

```ts
type Summary = {
  schema_version: "1.0";
  raw_row_count: number;
  trace_count: number;
  session_count: number;
  user_count: number;
  error_trace_count: number;
  empty_response_count: number;
  missing_response_count: number;
  skipped_row_count: number;
  duplicate_row_count: number;
};
type Topic = "sleep" | "blood_tests" | "body_composition"
           | "retinal_imaging" | "grip_strength" | "bone_density" | "nutrition"
           | "cardiovascular" | "pain" | "general_health" | "unclassified";
type TopicSource = "direct" | "previous_user_turn" | "semantic" | "unknown";
type TopicExclusion = "empty_prompt" | "chitchat" | "feedback" | "access_and_identity";
type TopicCount = { topic: Topic; trace_count: number; user_count: number; contextual_trace_count: number; semantic_trace_count: number };
type Mean = { count: number; mean: string | null }; // fixed-point decimal
type UnderstandingRequest = {
  understanding_intent: (
    "interpret_single_value" | "temporal_trend" | "cohort_comparison" |
    "literature_norm_comparison" | "panel_breakdown" | "concept_explanation"
  )[];
  test_target: {
    domain: "blood_tests" | "body_composition" | "sleep" | "retina_imaging" | "bone_density" | null;
    specific_metric: string | null;
  };
  resolution_source: "user_direct" | "session_context" | "agent_inferred" | "unresolved";
  target_position: number | null;
};
type TurnReview = {
  user_number: number;
  session_number: number;
  position: number;
  recorded_turn: number | null;
  next_position: number | null;
  execution_status: "completed" | "error" | "unknown";
  topics: Topic[];
  topic_source: TopicSource;
  topic_exclusion_reason: TopicExclusion | null;
  topic_anchor_position: number | null;
  topic_anchor_method: "direct" | "semantic" | null;
  intent: string;
  understanding_request: UnderstandingRequest | null;
  intent_source: "rule" | "semantic" | "unknown";
  state_signals: string[];
  delivery: string;
  next_user_action: string;
  satisfaction: "positive" | "negative" | "unknown";
  satisfaction_scope: "interaction" | "previous_reply" | "unknown";
  evidence: [deliveryReason: string, followUpReason: string];
  request_components: {
    kind: string;
    status: "addressed" | "reported_blocker" | "stated_boundary" | "omitted_without_explanation" | "execution_error" | "unknown";
    evidence: string;
  }[];
  conversation_signals: {
    kind: string;
    observation: string;
    related_position: number | null;
    categories: string[];
  }[];
};
type CrossSessionLink = {
  user_number: number;
  subject: string;
  kind: "repeated_request" | "same_intent_and_subject" | "shared_subject";
  order: "chronological" | "unknown";
  turns: [LinkTurn, LinkTurn];
  follow_through: "unknown" | "later_reply_addresses_topic" | "still_open_or_partial";
  evidence: string[];
};
type LinkTurn = Pick<TurnReview, "session_number" | "position" | "recorded_turn" | "intent" | "delivery">;
type UserSummary = {
  user_number: number;
  trace_count: number;
  session_count: number;
  intents: Record<string, number>;
  topics: Record<string, number>;
  single_observed_turn: Rate;
  single_observed_at_turn_1: Rate;
  error_trace_count: number;
  blank_reply_count: number;
  longest_observed_error_streak: number;
  recurring_topics: { subject: string; links: Omit<CrossSessionLink, "user_number" | "subject">[] }[];
  state_findings: {
    signal: string;
    focus: string;
    subject: string | null;
    subject_source: "user_prompt" | "earlier_user_prompt" | "agent_reply" | "semantic_topic" | "unknown";
    summary_he: string;
    repeated_after_incomplete_reply: boolean;
    evidence: {
      session_number: number;
      position: number;
      recorded_turn: number | null;
      subject_position: number | null;
      repeat_position: number | null;
    }[];
  }[];
  conversation_signal_counts: Record<string, number>;
  state: {
    signal: string;
    topic: Topic | null;
    trace_count: number;
    evidence: Pick<TurnReview, "session_number" | "position" | "recorded_turn" | "topic_source" | "topic_anchor_position">[];
  }[];
};
type Rate = { count: number; eligible_count: number; ratio: number | null };
type Distribution = { count: number; mean: number | null; median: number | null; p90: number | null };
type Analysis = {
  schema_version: "2.11";
  source: "local_json" | "langfuse_traces";
  summary: Summary;
  topics: TopicCount[];
  topic_population: {
    eligible_trace_count: number;
    excluded_trace_count: number;
    excluded_by_reason: Partial<Record<TopicExclusion, number>>;
    unclassified_trace_count: number;
    semantic_trace_count: number;
  };
  trace_cost_usd: Mean;
  trace_latency_seconds: Mean;
  latency_scope: "langfuse_trace" | "exported_trace";
  user_dynamics: {
    traces_per_user: Distribution;
    sessions_per_user: Distribution;
    single_observed_turn: Rate;
    single_observed_at_turn_1: Rate;
    inter_session_hours: Distribution;
    engagement_groups: Record<"one_trace" | "multiple_traces_up_to_10_sessions" | "over_10_sessions", number>;
  };
  time_distribution: {
    timezone: "UTC";
    traces_by_hour: number[]; // 24 bins
    between_00_and_06: Rate;
    between_01_and_04: Rate;
    missing_timestamp_count: number;
    between_00_and_06_by_topic: Record<string, Rate>;
    between_01_and_04_by_topic: Record<string, Rate>;
  };
  clinical_signals: {
    numeric_measurement_mention: Rate;
    concern_wording: Rate;
    urgency_wording: Rate;
    concern_or_urgency_wording: Rate;
    users_with_concern_or_urgency_wording: number;
    concern_or_urgency_traces_by_topic: Record<string, number>;
  };
  friction_metrics: {
    last_observed_turn_counts: Record<string, number>;
    last_observed_turn_1: Rate;
    exact_repeat_count: number;
    similar_reformulation_count: number;
    doctor_referral_wording: Rate;
    error_rate: Rate;
    repeat_after_incomplete: Rate;
    doctor_referral_followup_pushback: Rate;
    error_trace_count: number;
    users_with_errors: number;
    blank_reply_count: number;
    users_with_blank_replies: number;
  };
  cost_and_latency: {
    top_10_percent_user_cost: { eligible_user_count: number; top_user_count: number; cost_share: number | null };
    by_topic: Record<string, { trace_cost_usd: Mean; latency_seconds: Mean }>;
  };
  conversation: Record<"intent" | "delivery" | "next_user_action" | "satisfaction" | "state_signals", Record<string, number>>;
  state_users: Record<string, number>;
  state_finding_users_by_focus: Record<string, number>;
  understanding_requests: {
    trace_count: number;
    by_intent: Record<string, number>;
    by_domain: Record<string, number>;
    by_resolution_source: Record<string, number>;
  };
  conversation_signals: Record<string, {
    trace_count: number;
    known_user_count: number;
    observations: Record<string, number>;
  }>;
  request_coverage: {
    turns_with_components: number;
    component_count: number;
    by_status: Record<string, number>;
    by_component: Record<string, Record<string, number>>;
  };
  intents_by_reach: { intent: string; trace_count: number; session_count: number; user_count: number }[];
  cross_session: {
    link_count: number;
    user_count: number;
    users_with_repeated_request: number;
    links_by_kind: Record<string, number>;
  };
  review?: TurnReview[];
  user_summaries?: UserSummary[];
  cross_session_links?: CrossSessionLink[];
  topic_discovery?: {
    eligible_count: number;
    considered_count: number;
    candidates: {
      candidate_number: number;
      user_count: number;
      trace_count: number;
      evidence?: Pick<TurnReview, "user_number" | "session_number" | "position">[];
    }[];
  };
};
```

- Counts are nonnegative integers. Count distinct non-null account and session IDs independently. `error_trace_count` counts normalized `error`. Empty counts cover strings that trim to empty. Missing counts cover `None` only.
- Analysis averages available cost and latency values across accepted rows, including zero. Emit fixed-point strings rounded half-even to six decimal places, or `null` when count is zero. Both inputs may include trace-level costs. Label latency by source.
- M2 matches normalized user prompts only. Apply Unicode NFKC, case-folding, and whitespace collapsing. Match the following normalized substrings. When no direct topic matches, carry a single topic across consecutive recognized references within the same known account and session. Require consecutive recorded turn numbers, or require the current timestamp to be later than the preceding timestamp when either turn number is unavailable. Keep the position of the original explicit user prompt. A trace can count once in each matching topic. Count unique known users per topic. Include all topic rows in table order, including zero counts.

| Topic | Literal match phrases |
|---|---|
| `sleep` | `שינה`, `דום נשימה`, `sleep` |
| `blood_tests` | `בדיקות דם`, `בדיקת דם`, `כולסטרול`, `nightingale` |
| `body_composition` | `bmi`, `הרכב גוף`, `שומן ויסצרלי`, `שומן וויסצרלי`, `שומן הויסצרלי`, `שומן הוויסצרלי`, `משקל`, `dxa` |
| `retinal_imaging` | `רשתית`, `עורקים בעין`, `רוחב העורקים`, `retina` |
| `grip_strength` | `כוח אחיזה`, `כוח האחיזה`, `grip strength` |
| `unclassified` | No match or no prompt. |

Counts describe literal matches. Substrings may produce false positives. Follow-ups with implicit references can be unclassified. Do not equate these topic counts with the manual exploration.

User-state review groups evidence by signal and a single identified topic across known-user sessions. Use a null topic when no topic matches or when multiple topics make attribution ambiguous. Count each known account once per signal in aggregate output. User summaries are descriptive classifications of conversation evidence, with run-local references.

Conversation review groups by known account and session, orders turns per D-04, and evaluates adjacent rows. Treat absent account or session as isolated. If both recorded turn numbers exist and are nonconsecutive, withhold attribution. Keep execution status separate from delivery and satisfaction. The rule order and evidence codes are defined in `src/conversation.py`. Rules use text cues and abstain where signals are insufficient. They do not validate an answer's factual content. Optional review ordinals are run-local. Review output contains no source text or source IDs.

Cross-session review indexes subject matches from user prompts per known account. It excludes access and feedback turns. For each subject and distinct known session pair, rank matched turns by link kind and then token-set Jaccard overlap. Use the first pair in review order for ties. A repeat requires the same known intent and overlap ≥ 0.6. Distinct known timestamps determine order. Only a chronologically ordered repeat with an earlier open or partial reply can yield a follow-through label other than unknown. A later topical reply does not establish satisfaction.

## D-06 | Failure and packaging contracts

| Code | Trigger | Recovery |
|---|---|---|
| 0 | Successful processing or CLI help | None. |
| 2 | Invalid arguments or live configuration | Correct the named setting or argument. |
| 3 | Live HTTP, protocol, or pagination failure | Correct connectivity, permissions, or host compatibility. Rerun the full window. |
| 4 | Local read, encoding, JSON, or root schema failure | Provide a readable UTF-8 JSON array. |
| 5 | PDF path or rendering failure | Correct the output destination and rerun. |
| 6 | Local semantic model load or inference failure | Check model files and optional dependencies. |
| 1 | Unexpected application failure | Emit a generic sanitized message and investigate with synthetic input. |

- Use typed source, configuration, and report exceptions internally. The CLI translates failures to the table. Suppress raw exception text at this boundary.
- In M3, render aggregate counts, methodology, and limits. No raw excerpts or IDs. Write the complete PDF to a temporary file beside the destination, then atomically replace the destination. Clean up temporary output on failure.
- Use `ENTRYPOINT ["python", "-m", "src.main"]`. Install pinned packages at build time and copy only runtime code plus required assets. A local run must work with `--network none`. A live run contacts the configured Langfuse host.
