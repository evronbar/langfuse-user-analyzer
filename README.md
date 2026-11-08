# Langfuse User Analyzer

The CLI analyzes an assignment JSON export or traces fetched from Langfuse. It runs rule-based analysis locally on primarily Hebrew text. The default output contains aggregates. `--review` adds evidence codes and local sequence numbers. `--output` writes a Hebrew PDF from the same aggregates.

## Run on a local export

From the project directory:

```powershell
python -m src.main --input-json "C:/path/to/example.json" --summary-only
python -m src.main --input-json "C:/path/to/example.json" --review
python -m src.main --input-json "C:/path/to/example.json" --output output/pdf/report.pdf
```

Local analysis without a PDF or model uses Python 3.11 or later and the standard library. Install `requirements.txt` to render PDF. Local rendering uses Chromium, Chrome, or Edge. Install DejaVu Sans on Linux for Hebrew glyph coverage. Docker installs Chromium and the fonts. Keep the export outside the repository.

## Run against the Langfuse trace endpoint

Install the pinned dependencies with `python -m pip install -r requirements.txt`. Set `PHA_LF_HOST`, `PHA_LF_PUBLIC_KEY`, `PHA_LF_SECRET_KEY`, `PHA_LF_FROM`, and `PHA_LF_TO` in the shell or pass them to Docker.

```powershell
python -m src.main --output output/pdf/report.pdf
```

The adapter uses `GET /api/public/traces`, sends UTC `fromTimestamp` and `toTimestamp` bounds, and follows numbered pages. It preserves trace-level latency and cost when supplied. Use a Langfuse host that supports this legacy endpoint. Availability varies by deployment. Real-host validation requires valid project credentials. [Langfuse compatibility matrix](https://langfuse.com/self-hosting/upgrade/versioning)

## How the labels work

| Output | Input and decision |
|---|---|
| `intent` | First matching intent rule on the normalized user prompt. One primary label. |
| `state_signals` | Zero or more signals grounded in the prompt. A report, a measurement question, a clinical concern, and an action goal are separate categories. |
| `delivery` | Looks for a missing reply, a reported blocker, a stated boundary, a clarification request, or evidence of relevant content. `addressed` is a text match or measurement-content signal. It does not establish correctness or full fulfillment. |
| `next_user_action` | Compares the next turn in the same known account and session. Distinguishes repeat, refinement, clarification, apparent intent change, continuation, explicit feedback, missing evidence, and no observed next turn. |
| `satisfaction` and `satisfaction_scope` | Positive or negative only for recognized explicit wording. Test feedback is scoped to the interaction. Silence, generic thanks, a repeated question, and execution status leave satisfaction unknown. |
| `evidence` | A delivery reason code and a follow-up reason code. Source text is kept out of output. |

`intents_by_reach` counts turns, distinct known sessions, and distinct known users. Counts by category can share users or sessions. Review ordinals are assigned for each run, not stable identifiers. The next-position field is an observed neighbor, even when a gap in recorded turn numbers prevents attribution.

Rules can miss paraphrases, multi-part requests, sarcasm, or references that require earlier context. Check review labels against the source before drawing conclusions. The classifier does not validate medical claims or infer diagnoses.

### Coverage of individual request components

`request_coverage` counts detected components by kind and status. `--review` adds `request_components` with a reason code for each detected part. A reply can supply one component and leave another unresolved. The supported rules distinguish an answer cue, a reported blocker, a stated boundary, an omission without explanation, and an empty reply with error status. Use `unknown` when the evidence is ambiguous. A reported blocker describes what the agent claimed, and an omission describes the visible reply. These labels do not infer an internal cause.

## Links across conversations

`cross_session` counts links, distinct users with a link, and distinct users with a detected repeat. `--review` adds `cross_session_links` with run-local message positions and evidence codes. Each link requires two known sessions from the same account and a specific shared subject in the user prompts. A repeat also requires the same detected intent and at least 60% token-set overlap. Weaker evidence produces a shared-subject or same-intent-and-subject link.

The tool keeps one strongest link per subject and session pair. Each review user summary exposes its links grouped by subject under `recurring_topics`. It compares reply coverage over time only for a repeated request whose earlier reply was open or partial and whose matched turns have distinct timestamps. Later topical evidence does not establish full resolution or satisfaction. Subjects outside the rule list and context mentioned only by the agent are excluded from matching.

## Topic context and user-state review

An explicit topic in the current user prompt takes precedence. A recognized backward reference can carry a single known topic from the previous turn in the same conversation. Consecutive references retain the original anchor. An intervening unrelated or excluded turn, an ambiguous anchor, or a gap in turn numbers breaks the chain. Text from the agent does not supply a user-topic anchor. Topic counts include separate `contextual_trace_count` and `semantic_trace_count` subsets. `topic_population` separates empty prompts, standalone greetings, feedback and access/identity turns from eligible unclassified turns. Excluded turns still contribute to applicable non-topic metrics.

With `--review`, each turn has `topics`, `topic_source`, and `topic_anchor_position`. `user_summaries` groups state signals by known user and topic across sessions. Each group points to the supporting turn positions. Questions about a measurement, clinical questions, explicitly expressed concern, self-reports, and action goals remain separate labels. Ambiguous or unassigned topics appear as `null` in user-state groups. Default output keeps only aggregate user-state counts in `state_users`.

To assess quality on new real conversations, select and manually label a sample before inspecting the automated results. Compare topics, their sources, and state signals using the run-local positions in `--review`. Count incorrect labels and unknowns separately. Keep the sample and review outside the repository. The checked-in tests use synthetic conversations.

`state_findings` adds short Hebrew findings to each review summary. Templates describe what was reported, asked, or expressed and use only a controlled set of measurement names. Each finding points to user-authored evidence, records whether a subject came from the user, a prior turn, a local model suggestion, or the agent reply, and can point to a repeated question after incomplete coverage. Agent-only context is attributed separately and does not modify the topic assigned to the user's message. Aggregate output counts distinct users by finding focus.

`understanding_requests` counts recognized test-understanding requests by intent, target domain, and resolution source. In `--review`, each `understanding_request` records all detected intents and a test target with a domain and specific metric. A broad request can have no specific intent from the supported list. An ambiguous metric stays null even if its domain is known. A referenced target may carry forward within the same known session. Targets inferred from the agent's reply are marked separately. Because a request can carry multiple intents, intent counts may exceed the number of requests.

`conversation_signals` reports first visible appearances of measurements and numeric values in agent replies, ambiguity around bare numbers, temporal questions, concern wording, medication requests, retina explanation requests, cross-domain questions, disclaimer follow-ups, large reply-length gaps, nonempty error replies, and questions about terms introduced in the preceding reply. Historical checks use the visible session. First appearance does not verify whether the agent retrieved, inferred, or invented a value. Time and number cues do not verify the comparison. Follow-up checks require adjacent turns in the same known session. A large length gap uses a prompt of at most 12 words and a reply of at least 200 words. Explicit requests for brevity are counted separately.

## Activity, friction, and performance metrics

| Output | Definition |
|---|---|
| `user_dynamics` | Observed trace and session distributions across known accounts. P90 uses the nearest rank. The engagement bins are one trace, multiple traces with up to ten observed sessions, and more than ten observed sessions. A one-record session and a one-record session starting at turn 1 are counted separately. Inter-session hours cover gaps between observed timestamp spans. |
| `time_distribution` | UTC hourly counts and shares in the 00:00–06:00 and 01:00–04:00 windows, overall and by topic. Window starts are inclusive and ends exclusive. The denominator includes only timestamped records. |
| `clinical_signals` | Pattern matches for numeric measurement mentions and concern or urgency wording in nonblank user prompts. These fields describe language in the dataset. |
| `friction_metrics` | Error rate is based on all traces. The referral rate is based on nonblank replies. Repeats after incomplete replies and pushback after referrals use their respective subsets with an observed adjacent nonblank follow-up. Referral pushback describes user wording and cannot establish whether the referral was appropriate. The last observed turn may reflect the input window. |
| `cost_and_latency` | Available samples and means by topic. Cost concentration uses the top `ceil(eligible users / 10)` known users with recorded costs. Missing costs are excluded. |

Rates return a numerator, an eligible denominator, and a ratio from 0 to 1. A missing denominator produces `null`. Multi-topic records can contribute to multiple topic rows. In `--review`, user summaries also show topic counts, one-record sessions, errors, blank replies, and the longest observed consecutive error streak within one session. Interpret all results within the observed data window.

## Optional local semantic analysis

Install the optional dependencies with `python -m pip install -r requirements-semantic.txt`. Save the E5 tokenizer and quantized weights to an ignored local folder such as `data/multilingual-e5-small`. Use [tokenizer.json](https://huggingface.co/Xenova/multilingual-e5-small/blob/main/tokenizer.json) and [model_int8.onnx](https://huggingface.co/Xenova/multilingual-e5-small/blob/main/onnx/model_int8.onnx).

```powershell
python -m src.main --input-json "C:/path/to/example.json" --review --semantic-model data/multilingual-e5-small
```

Existing direct rules and eligible same-session topic anchors take precedence. The model compares user text with labeled examples and abstains below a similarity floor or when two categories are too close. Accepted semantic topics contribute to the aggregate counts used by the PDF. Review output marks accepted classifications as `semantic`. The application only reads model files from disk and makes no model-service calls.

Unassigned turns can appear in `topic_discovery.candidates` when similar prompts come from at least three known users. Each candidate includes counts and, in review mode, supporting turn positions. Review and name the candidate before adding it to the taxonomy. The first 500 eligible turns are considered per run.

To regenerate and score the synthetic fixture from the project directory:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python tests/evaluate_synthetic.py --output data/synthetic-evaluation --semantic-model data/multilingual-e5-small
```

Omit `--semantic-model` to evaluate rules alone. The generated files contain synthetic traces, expected annotations, and scores. The fixture tests a small, fixed set of behaviors and does not establish accuracy on live conversations.

## Optional Langfuse tracing

Build the image again after updating the source. Set `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` in the current shell using an ingestion key for a separate telemetry project. Keep the assignment's `PHA_LF_*` variables for reading source conversations. Use a fresh key stored outside the repository.

```bash
docker build -t langfuse-user-analyzer:local .
printf '%s\n' '[]' | docker run --rm -i --pull never \
  -e LANGFUSE_BASE_URL -e LANGFUSE_PUBLIC_KEY -e LANGFUSE_SECRET_KEY \
  langfuse-user-analyzer:local --input-json /dev/stdin --summary-only --trace
```

The smoke test uses an empty synthetic export. Inspect `user-analyzer.run` in the telemetry project's Tracing view under the `user-analyzer` environment. The SDK flushes on CLI shutdown. Spans contain fixed step names, durations, run-mode flags, and generic status. Prompts, responses, study identifiers, analysis results, paths, credentials, exception text, and embeddings stay out of span payloads. Offline runs can omit `--trace`.

## Docker and verification

```powershell
docker build -t langfuse-user-analyzer .
$out = (New-Item -ItemType Directory -Force output/pdf).FullName
docker run --rm --env-file "C:/path/outside-repo/langfuse.env" --mount "type=bind,source=$out,target=/output" langfuse-user-analyzer
docker run --rm --network none --mount "type=bind,source=C:/path/to/example.json,target=/input/example.json,readonly" --mount "type=bind,source=$out,target=/output" langfuse-user-analyzer --input-json /input/example.json --output /output/report.pdf
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

The container defaults to `/output/report.pdf`, including when Docker arguments replace the image's default command. The host output folder must be writable by container UID 10001, or on Linux run with `--user "$(id -u):$(id -g)"` and a folder writable by that user. The image includes PDF and semantic dependencies. To use E5, bind-mount only the directory containing `tokenizer.json` and `model_int8.onnx` at `/model` read-only, then pass `--semantic-model /model`. Model weights and example data stay outside the image.

To generate a PDF from saved UTF-8 aggregate JSON, run `python -m src.reporter --input-json analysis.json --output output/pdf/report.pdf`. The renderer uses embedded DejaVu Sans on Linux and can use Arial on Windows. It consumes aggregate data only.
