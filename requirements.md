# System requirements | Langfuse User Analyzer

```yaml
spec_version: 0.2.0
status: draft
milestones: [M1, M2, M3]
current_milestone: M3
requirement_kinds: [assignment, project]
```

## Assignment obligations

| ID | Stage | EARS requirement |
|---|---|---|
| A-01 | M1 | The system shall retrieve trace records from Langfuse for the configured time range. |
| A-02 | M1 | While live input is selected, the system shall read its connection configuration exclusively from `PHA_LF_HOST`, `PHA_LF_PUBLIC_KEY`, `PHA_LF_SECRET_KEY`, `PHA_LF_FROM`, and `PHA_LF_TO`. |
| A-03 | M1–M3 | The system shall keep example data out of the source repository and distribution image. |
| A-04 | M2–M3 | The system shall analyze traces and produce a PDF about user requests and what the conversations reveal about users' concerns and reported state. |
| A-05 | M3 | The submission shall include a GitHub project link, a Docker image, and the PDF that the image generated from the supplied example data. |

## M1 | Input and summary

| ID | EARS requirement |
|---|---|
| M1-01 | When the operator selects `--input-json PATH`, the system shall read a UTF-8 JSON array from that path and run without network access or Langfuse credentials. |
| M1-02 | When the operator runs without `--input-json`, the system shall validate the five connection settings before opening an HTTP connection. |
| M1-03 | If a setting is missing, invalid, or specifies a start at or after the end, then the system shall exit with code 2 and identify the setting without displaying its value. |
| M1-04 | When using Langfuse live, the system shall call `GET /api/public/traces` with both time bounds and request numbered pages through `meta.totalPages`. |
| M1-05 | If a page fails with a retryable condition, then the system shall retry that page within the bounded policy in `design.md`. |
| M1-06 | If source loading fails or pagination metadata is invalid, then the system shall exit nonzero and emit no partial summary. |
| M1-07 | When either adapter loads rows, the system shall normalize them using the same field rules and count duplicate trace IDs separately from distinct messages with identical text. |
| M1-08 | If a row lacks an eligible trace ID, then the system shall skip it and increment the skipped-row count. If optional fields are missing or invalid, then the system shall represent them as unknown. |
| M1-09 | When a source returns no accepted records, the system shall produce a valid zero-count summary. |
| M1-10 | When source processing succeeds, the system shall write exactly one aggregate JSON object to stdout. When processing fails, the system shall write a sanitized diagnostic to stderr. |
| M1-11 | When a recorded response is empty, the system shall retain the trace and count the empty response independently of its status. |
| M1-12 | While operating on any source, the system shall keep raw text, identifiers, and credentials out of logs and exported summaries. |

**M1 acceptance:** The supplied local sample produces 26 traces, 14 sessions, 8 users, and 2 error traces. A synthetic multi-page test proves that the adapter follows numbered pages and retains the required trace fields. Verify the endpoint and field coverage on the evaluator host separately.

## M2 | Descriptive analytics

| ID | EARS requirement |
|---|---|
| M2-01 | When analysis runs, the system shall count configured topic matches in the user prompts, with separate message and distinct-user counts. |
| M2-02 | If no topic matches a prompt, then the system shall count that trace under `unclassified`. |
| M2-03 | When calculating cost or latency statistics, the system shall exclude unavailable observations from each corresponding denominator and report the denominator. |
| M2-04 | While presenting analytical results, the system shall distinguish mentions of measurements and reported concerns from clinical findings. |
| M2-05 | When documenting a user-state observation, the system shall identify what the user reported, expressed concern about, or tried to clarify, and attribute context supplied only by the agent to the agent. |
| M2-06 | When evaluating reactions to a reply, the system shall distinguish explicit feedback from a follow-up behavior and from absent evidence. |
| M2-07 | When the operator selects `--review`, the system shall emit per-turn labels and evidence codes with run-local positions. |
| M2-08 | If adjacent recorded turn numbers are nonconsecutive, then the system shall withhold follow-up and satisfaction attribution. |
| M2-09 | When reporting detected intent, the system shall count messages, distinct known sessions, and distinct known users separately. |
| M2-10 | When different known sessions from the same account contain a shared specific subject in user prompts, the system shall report the strongest detected link per subject and session pair. |
| M2-11 | If matching turns lack reliable chronological order or evidence of repetition, then the system shall leave cross-session follow-through unknown. |
| M2-12 | When a user explicitly refers back to a single identifiable topic in the preceding turn of the same conversation, the system shall preserve the topic's original user-authored anchor. |
| M2-13 | If the topic is ambiguous or a topic appears only in an agent reply, then the system shall leave it unassigned unless a user prompt explicitly identifies it. |
| M2-14 | When the operator requests review output, the system shall group state signals per known user and identify the supporting turns. |
| M2-15 | When the operator selects a local semantic model, the system shall propose topic or intent labels for eligible turns left unclassified by existing rules and record semantic provenance. |
| M2-16 | When semantically similar unassigned prompts appear across at least three known accounts, the system shall expose a candidate with counts and optional supporting turn positions. |
| M2-17 | If the configured local model cannot be loaded or evaluated, then the system shall emit a sanitized error and exit with code 6. |
| M2-18 | When supported explicit request components are detected, the system shall label each component separately and include a reason code in review output. |
| M2-19 | If evidence is insufficient to attribute a blocker, boundary, or omission to a component, then the system shall leave its coverage unknown. |
| M2-20 | When a user-state signal is detected, the system shall express it as a short finding and preserve its supporting turn positions in review output. |
| M2-21 | When a measurement name is available only in the agent reply, the system shall attribute it to the agent and retain the user-authored question as separate evidence. |
| M2-22 | When recognizable data, user-state, or follow-up patterns appear, the system shall count them by observation and provide turn-level evidence in review output. |
| M2-23 | If an agent reply introduces a measure or value absent from the visible session history, then the system shall report its first visible appearance without asserting its origin or accuracy. |
| M2-24 | When a test-understanding request is recognized, the system shall report each supported understanding intent, its target domain and metric when known, and the source of target identification. |
| M2-25 | If the target metric is ambiguous or absent, then the system shall retain the request and leave the unresolved target fields null. |

## M3 | Reporting and delivery

| ID | EARS requirement |
|---|---|
| M3-01 | When the operator supplies `--output PATH.pdf`, the system shall create a PDF from the same aggregate data used in the CLI summary. |
| M3-02 | If PDF generation fails, then the system shall exit with code 5, preserve any existing PDF at the target path, and remove temporary output. |
| M3-03 | When run in Docker with a read-only input mount and a writable output mount, the system shall generate the sample PDF with networking disabled. |
| M3-04 | While the image runs in live mode, the system shall require network access only to the configured Langfuse host and shall install no runtime dependencies. |
| M3-05 | When producing a report, the system shall render readable right-to-left Hebrew using an embedded Hebrew-capable font. |

## Accepted scope for user-state exploration

- User state means what users report experiencing, what concerns or interests them, and what they try to clarify about their situation. A question may establish a goal or concern. It does not establish a diagnosis or measured value.
- Keep user text, agent text, and execution status separate. Keep request intent, observed next user action, and evidence of satisfaction separate in review annotations. Interpret a subsequent user turn in the same conversation and attribute explicit feedback only to the answer or interaction it addresses.
- A repeated or reframed request may show the task is still open. It does not establish dissatisfaction. Missing follow-up, additional questions, and execution success do not establish satisfaction. Use unknown when explicit evidence is absent.

## Decisions requiring review

- Live data mapping is pending real-host verification. Confirm availability of the legacy trace endpoint and coverage of application I/O, metadata, latency, and cost. Preserve missing fields as unknown.
- Whether to expand the initial automated labels after review on unseen conversations. The hand-labeled sample guided rule development and cannot establish accuracy on new data.
