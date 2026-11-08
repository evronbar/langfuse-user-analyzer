# Implementation checklist | Langfuse User Analyzer

```yaml
spec_version: 0.2.0
status: draft
current_milestone: M3
authorized_by_this_spec: []
```

Execute only an explicitly authorized milestone. Complete tasks in listed order. Reuse files that already satisfy the contract. Mark a task complete only after its verification passes. Never add the supplied example data to Git, the build context, or committed tests.

## M1 | Runnable ingestion and summary

- [ ] **T01 | Scaffold the package and exclusions.** Create the source package, Git and Docker exclusions, and a dependency file with the M1 pins. Add synthetic tests under `tests/`. **Verify:** The clean environment installs the pinned dependencies. Git excludes dummy `.env`, `.json`, `.csv`, and `.pdf` files. Docker context exclusions cover the same patterns.
- [ ] **T02 | Implement records and shared normalization.** Add `LiveConfig`, `Trace`, and `TraceBatch` plus the D-04 validation rules. **Verify:** Synthetic tests cover malformed rows, duplicate IDs, repeated prompts with distinct IDs, missing fields, booleans in numeric fields, status precedence, and the batch invariant.
- [ ] **T03 | Implement local CLI end to end.** Add UTF-8 loading, argument parsing, `summarize`, and sanitized error handling. **Verify:** The supplied local example produces 26 traces, 14 sessions, 8 users, and 2 error traces. An empty array yields a zero summary. Invalid local inputs exit 4 with no stdout. The offline path works without keys or network.
- [ ] **T04 | Verify evaluator-host trace access.** Check endpoint compatibility and coverage of chat I/O, application metadata, session IDs, latency, and cost without exporting any rows.
- [x] **T05 | Implement trace-list HTTP and numbered pages.** Synthetic checks cover pagination, empty windows, trace field mapping, bounded retries, mismatched page metadata, and an unavailable endpoint.
- [ ] **T06 | Package and smoke-test the M1 CLI.** Build the runtime image from the selected base and resolved exact pins. Document both input modes. **Verify:** The local sample summary from a read-only mount with `--network none` matches the host summary. Inspect the image and build context for sample data and secrets. Smoke-test the live path with provided credentials when available. Record pending live verification explicitly.

## M2 | Descriptive analysis

- [ ] **T07 | Confirm topic rules, metrics, and annotation criteria.** Compare literal matches with the manual review. Keep reported user state, request intent, follow-up behavior, and feedback separate. **Verify:** Each candidate label has an evidence rule and example. Preserve unknown for satisfaction unless the user explicitly evaluates the relevant answer or interaction. Review agreement on borderline cases before adding automated labels.
- [ ] **T08 | Add topic and operational aggregates.** Implement `analyze` and switch successful CLI output to the v2 DTO. **Verify:** Synthetic cases cover Hebrew and English matches, repeated mentions, multiple matches, unclassified prompts, distinct-user counts, missing numerics, zero values, and six-place decimal means. Recheck the sample summary nested in the v2 DTO.
- [x] **T08a | Implement the first conversation classifier.** Add local intent and state signals, delivery evidence, same-session follow-up behavior, and explicit feedback scope. **Verified:** Synthetic tests and a per-turn comparison with all 26 manually labeled sample messages. Validation on unseen conversations remains open.

## M3 | PDF and submission

- [x] **T09 | Confirm report language and contents.** Hebrew report using aggregate topics, user-state findings, concern and urgency rates, and request coverage.
- [x] **T10 | Add the PDF renderer and CLI option.** Verified local rendering from traces and aggregate JSON, visual RTL layout with Arial and DejaVu Sans, embedded fonts, and preservation of an existing PDF on render failure. Container verification is tracked in T11.
- [ ] **T11 | Build and reproduce final output.** Run the image on the supplied data with a read-only input mount and writable output mount. **Verify:** The PDF opens, its counts match the CLI output, and its content contains no raw excerpts or identifiers. Reproduce it without network access.
- [ ] **T12 | Prepare the three submission deliverables.** Finalize run instructions and package the repository link, Docker image, and sample PDF. **Verify:** A clean build reproduces the documented run and the repository and image contain no source data or keys. Confirm the evaluator's image delivery format before distribution.
