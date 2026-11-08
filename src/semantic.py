from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import Trace


Vector = tuple[float, ...]


class SemanticError(Exception):
    """Local model could not be loaded or evaluated."""


class Encoder(Protocol):
    def encode(self, texts: Sequence[str]) -> list[Vector]: ...


class LocalE5:
    def __init__(self, directory: Path) -> None:
        try:
            import numpy
            import onnxruntime
            from tokenizers import Tokenizer

            self.numpy = numpy
            self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
            pad = self.tokenizer.token_to_id("<pad>")
            if pad is None:
                raise SemanticError("tokenizer has no padding token")
            self.tokenizer.enable_padding(pad_id=pad, pad_token="<pad>")
            self.tokenizer.enable_truncation(max_length=256)
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 2
            self.model = onnxruntime.InferenceSession(
                str(directory / "model_int8.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self.inputs = {value.name for value in self.model.get_inputs()}
        except ImportError:
            raise SemanticError("install the optional semantic dependencies") from None
        except Exception:
            raise SemanticError("could not load the local model files") from None

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        vectors = []
        np = self.numpy
        for start in range(0, len(texts), 16):
            batch = self.tokenizer.encode_batch(["query: " + text for text in texts[start:start + 16]])
            mask = np.array([row.attention_mask for row in batch], dtype=np.int64)
            arrays = {
                "input_ids": np.array([row.ids for row in batch], dtype=np.int64),
                "attention_mask": mask,
                "token_type_ids": np.array([row.type_ids for row in batch], dtype=np.int64),
            }
            hidden, = self.model.run(["last_hidden_state"], {key: arrays[key] for key in self.inputs})
            pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
            unit = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            vectors.extend(tuple(vector) for vector in unit.tolist())
        return vectors


TOPIC_EXAMPLES = {
    "sleep": ("אני מתקשה להירדם ומתעורר הרבה בלילה", "ישנתי מעט וקמתי מותשת", "מה אומרים מדדי איכות השינה והנשימה בלילה"),
    "blood_tests": ("מה המשמעות של הערכים שקיבלתי מהמעבדה בבדיקת הדם", "איך לפרש את רמת הכולסטרול או הסוכר בדם"),
    "body_composition": ("מה משמעות אחוז השומן והשינוי במשקל שלי", "רוצה להבין את השומן הבטני ואת מסת השריר"),
    "retinal_imaging": ("מה מראה צילום הרשתית על כלי הדם בעין", "איך להבין את מדדי העורקים ברשתית"),
    "grip_strength": ("כוח הלחיצה והאחיזה שלי ביד ירד", "למה קשה לי להחזיק חפצים ואיך מחזקים את האחיזה"),
    "unknown": (
        "השאלה קשורה לחשבון או לכניסה לשירות", "יש תקלה טכנית באפליקציה או בחיבור למכשיר",
        "איפה מורידים מסמך או רואים היסטוריה באפליקציה", "תודה על העזרה", "מה זה אומר",
        "איך מבטלים או מזיזים פגישה", "שאלה על תרופות או נושא רפואי אחר",
    ),
}
INTENT_EXAMPLES = {
    "interpret_measurement": ("אני מנסה להבין מה התוצאה הזאת אומרת עליי", "איך לקרוא את המספר שהופיע בבדיקה"),
    "compare_or_benchmark": ("איך הנתון שלי עומד לעומת אנשים אחרים", "האם אני מעל הממוצע של קבוצת המחקר"),
    "next_action": ("אילו צעדים כדאי לנקוט כדי לשפר את המצב", "יש הרגל שאפשר לשנות כדי להשפיע על התוצאה"),
    "general_explanation": ("מה גורם לתופעה הזאת באופן כללי", "למה זה קורה ומה יכול להשפיע"),
    "retrieve_or_locate": ("איפה אפשר למצוא את הדוח שלי", "אני רוצה שתציג לי את התוצאות המלאות"),
    "clinical_question": ("האם המדידה מעידה שיש לי בעיה רפואית", "האם ייתכן שאני סובלת מהפרעה כזאת"),
    "define_term": ("אפשר להסביר לי מה פירוש המונח הזה", "אני לא מבין את ראשי התיבות"),
    "unknown": ("אני רק מתאר מה חוויתי", "תודה על ההסבר", "נתקעתי בהתחברות", "אין לי שאלה כרגע"),
}


def cosine(left: Vector, right: Vector) -> float:
    return sum(x * y for x, y in zip(left, right, strict=True))


def closest(vector: Vector, references: list[tuple[str, Vector]], floor: float, margin: float) -> str | None:
    best: dict[str, float] = {}
    for label, reference in references:
        best[label] = max(best.get(label, -1.0), cosine(vector, reference))
    (label, score), (_, runner_up), *_ = sorted(best.items(), key=lambda pair: -pair[1])
    return label if label != "unknown" and score >= floor and score - runner_up >= margin else None


@dataclass(frozen=True, slots=True)
class SemanticSuggestion:
    topic: str | None
    intent: str | None
    vector: Vector


def suggestions(traces: tuple[Trace, ...], encoder: Encoder) -> dict[str, SemanticSuggestion]:
    prompts = {trace.trace_id: trace.prompt.strip() for trace in traces if trace.prompt and trace.prompt.strip()}
    topic_refs = [(label, example) for label, examples in TOPIC_EXAMPLES.items() for example in examples]
    intent_refs = [(label, example) for label, examples in INTENT_EXAMPLES.items() for example in examples]
    examples = [text for _, text in topic_refs + intent_refs]
    unique_prompts = list(dict.fromkeys(prompts.values()))
    try:
        vectors = encoder.encode(examples + unique_prompts)
    except Exception:
        raise SemanticError("local inference failed") from None
    topic_end, intent_end = len(topic_refs), len(examples)
    topics = list(zip((label for label, _ in topic_refs), vectors[:topic_end], strict=True))
    intents = list(zip((label for label, _ in intent_refs), vectors[topic_end:intent_end], strict=True))
    by_prompt = dict(zip(unique_prompts, vectors[intent_end:], strict=True))
    return {
        key: SemanticSuggestion(closest(vector, topics, 0.82, 0.022), closest(vector, intents, 0.83, 0.025), vector)
        for key, prompt in prompts.items() for vector in (by_prompt[prompt],)
    }


@dataclass(frozen=True, slots=True)
class CandidateTurn:
    account: str
    user_number: int
    session_number: int
    position: int
    vector: Vector


def discover(rows: list[CandidateTurn], *, include_review: bool) -> dict[str, object]:
    # Complete linkage prevents a chain of weakly related messages from
    # combining into one topic. Restrict the quadratic scan to 500 turns.
    clusters: list[list[CandidateTurn]] = []
    for turn in rows[:500]:
        for group in clusters:
            if all(cosine(turn.vector, member.vector) >= 0.86 for member in group):
                group.append(turn)
                break
        else:
            clusters.append([turn])
    candidates = []
    for group in clusters:
        users = len({turn.account for turn in group})
        if users < 3:
            continue
        entry: dict[str, object] = {
            "candidate_number": len(candidates) + 1, "user_count": users, "trace_count": len(group),
        }
        if include_review:
            entry["evidence"] = [
                {"user_number": t.user_number, "session_number": t.session_number, "position": t.position}
                for t in group
            ]
        candidates.append(entry)
    return {"eligible_count": len(rows), "considered_count": min(500, len(rows)), "candidates": candidates}
