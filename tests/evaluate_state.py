"""Compare hand-labeled state evidence with analysis output on local data."""

import argparse
import json
from collections import Counter
from pathlib import Path

from src.analyzer import analyze
from src.fetcher import normalize_rows, read_local
from src.semantic import LocalE5


SIGNALS = ("self_report", "expressed_concern", "measurement_question", "clinical_concern", "action_goal")


def evaluate(result, gold):
    expected = {(r["user"], r["session"], r["turn"]): r for r in gold}
    observed = {
        (r["user_number"], r["session_number"], r["recorded_turn"]): set(r["state_signals"])
        for r in result["review"]
    }
    if len(expected) != len(gold) or len(observed) != len(result["review"]) or expected.keys() != observed.keys():
        raise ValueError("gold and review coordinates must cover the same unique turns")

    summarized = {
        (user["user_number"], ref["session_number"], ref["recorded_turn"], state["signal"])
        for user in result["user_summaries"]
        for state in user["state"]
        for ref in state["evidence"]
    }
    review_labels = {(*key, label) for key, labels in observed.items() for label in labels}
    if summarized != review_labels:
        raise ValueError("user summaries must preserve review labels and evidence coordinates")

    totals = {label: Counter() for label in SIGNALS}
    rows = []
    for key, annotation in expected.items():
        correct = set(annotation["labels"])
        predicted = observed[key]
        for label in correct | predicted:
            outcome = "match" if label in correct and label in predicted else "missed" if label in correct else "extra"
            totals[label][outcome] += 1
        rows.append({
            **annotation, "actual": sorted(predicted),
            "missed": sorted(correct - predicted), "extra": sorted(predicted - correct),
        })
    counts = sum(totals.values(), Counter())
    match, missed, extra = (counts[key] for key in ("match", "missed", "extra"))
    return {
        "turn_count": len(rows),
        "exact_turn_count": sum(not row["missed"] and not row["extra"] for row in rows),
        "matched_labels": match, "missed_labels": missed, "extra_labels": extra,
        "precision": round(match / (match + extra), 6) if match + extra else None,
        "recall": round(match / (match + missed), 6) if match + missed else None,
        "by_signal": {
            label: {key: count[key] for key in ("match", "missed", "extra")} for label, count in totals.items()
        },
        "summary_evidence_matches_review": True,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--semantic-model", type=Path)
    args = parser.parse_args()
    batch = normalize_rows(read_local(args.input_json))
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    outcomes = {"rules": evaluate(analyze(batch, "local_json", include_review=True), gold)}
    if args.semantic_model is not None:
        encoder = LocalE5(args.semantic_model)
        outcomes["semantic"] = evaluate(analyze(batch, "local_json", include_review=True, encoder=encoder), gold)
    args.output.write_text(json.dumps(outcomes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        mode: {key: value for key, value in result.items() if key != "rows"}
        for mode, result in outcomes.items()
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
