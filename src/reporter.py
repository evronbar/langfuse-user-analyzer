"""Render aggregate analysis as a Hebrew HTML-to-PDF report."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from base64 import b64encode
from collections.abc import Mapping, Sequence
from html import escape
from io import BytesIO
from pathlib import Path
from string import Template
from tempfile import TemporaryDirectory

from .models import ReportError


TOPICS = {
    "sleep": "שינה", "blood_tests": "בדיקות דם", "body_composition": "הרכב גוף",
    "retinal_imaging": "הדמיית רשתית", "grip_strength": "כוח אחיזה", "bone_density": "צפיפות עצם",
    "nutrition": "תזונה", "cardiovascular": "לב וכלי דם", "pain": "כאבים", "general_health": "בריאות כללית",
}
FOCUSES = {
    "compare_change_with_cohort": "השוואת שינוי לקבוצת המחקר",
    "compare_with_cohort": "השוואת מדידה לקבוצת ייחוס",
    "interpret_change": "פירוש שינוי בין בדיקות",
    "interpret_percentile": "הבנת אחוזון",
    "interpret_panel": "פירוש ערכי פאנל",
    "interpret_measurement": "פירוש מדידה",
    "possible_action": "בירור מה אפשר לעשות",
    "clinical_question": "בירור אם יש עדות למצב רפואי",
    "sleep_interruption": "דיווח על יקיצות בלילה",
    "late_sleep_onset": "דיווח על הירדמות מאוחרת",
    "self_report": "דיווח אישי",
    "expressed_concern": "דאגה שהובעה במפורש",
}
COVERAGE = {
    "reported_blocker": "חסם שהסוכן דיווח עליו",
    "stated_boundary": "מגבלה שהסוכן הציב",
    "omitted_without_explanation": "רכיב שלא קיבל התייחסות או הסבר",
    "execution_error": "שגיאת ביצוע עם תשובה ריקה",
    "addressed": "זוהתה התייחסות לרכיב",
    "unknown": "כיסוי הבקשה לא ברור",
}

def fields(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def quantity(amount: int, singular: str, plural: str) -> str:
    return f"{singular} 1" if amount == 1 else f"{amount:,} {plural}"


def format_rate(value: object) -> tuple[str, str]:
    data = fields(value)
    numerator, denominator = count(data.get("count")), count(data.get("eligible_count"))
    if not denominator or numerator > denominator:
        return "-", "-"
    return f"{numerator:,} / {denominator:,}", f"{numerator / denominator:.1%}"


def rate_card(title: str, detail: str, rate: object) -> str:
    fraction, percent = format_rate(rate)
    detail_line = detail if fraction == "-" else f'<bdi dir="ltr">{fraction.replace(" ", "")}</bdi> {detail}'
    return (f'<div class="card"><h3>{title}</h3><b dir="ltr" style="text-align:right">{percent}</b>'
            f'<small class="rate-detail" dir="rtl">{detail_line}</small></div>')


def progress(label: str, amount: int, base: int, singular: str, plural: str) -> str:
    width = min(100, 100 * amount / base) if base else 0
    return (f'<div class="metric"><div class="metric-head"><span>{escape(label)}</span>'
            f'<b class="metric-count" dir="rtl">{quantity(amount, singular, plural)}</b></div><div class="track">'
            f'<div class="fill" style="width:{width:.1f}%"></div></div></div>')


def attention_summary(clinical: Mapping[str, object]) -> tuple[str, str]:
    _, combined = format_rate(clinical.get("concern_or_urgency_wording"))
    _, concern = format_rate(clinical.get("concern_wording"))
    _, urgency = format_rate(clinical.get("urgency_wording"))
    zero = combined != "-" and count(fields(clinical.get("concern_or_urgency_wording")).get("count")) == 0
    if combined == "-" and concern != "-" and urgency != "-":
        zero = all(count(fields(clinical.get(key)).get("count")) == 0
                   for key in ("concern_wording", "urgency_wording"))
    if zero:
        return "0%", "לא אותרו ביטויי חשש או דחיפות בהודעות המשתמשים."
    if combined != "-":
        return combined, "שיעור ההודעות הלא ריקות שבהן אותר לפחות ביטוי אחד של חשש או דחיפות."
    if concern != "-" or urgency != "-":
        return "—", f"בפלט זה חושבו שיעורים נפרדים. ביטויי חשש {concern}, ביטויי דחיפות {urgency}."
    return "—", "אין הודעות זמינות לחישוב השיעור."


def topic_chart(rows: list[tuple[str, int]]) -> str:
    if not rows:
        return '<p class="note band">לא נמצאו נושאים מזוהים בפניות שנבדקו.</p>'
    from bidi.algorithm import get_display
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import MaxNLocator

    fig = Figure(figsize=(7.2, 0.36 * len(rows) + 0.55), dpi=160, facecolor="white", layout="constrained")
    canvas = FigureCanvasAgg(fig)
    axis = fig.subplots()
    labels, values = zip(*rows, strict=True)
    bars = axis.barh(range(len(rows)), values, height=0.62,
                     color=["#135a9e"] + ["#3987b6"] * (len(rows) - 1))
    axis.invert_yaxis()
    axis.invert_xaxis()
    axis.set_xlim(max(values) * 1.23, 0)
    axis.yaxis.tick_right()
    axis.set_yticks(range(len(rows)), [get_display(label, base_dir="R") for label in labels])
    axis.xaxis.set_major_locator(MaxNLocator(nbins=6, integer=True))
    axis.set_axisbelow(True)
    axis.grid(axis="x", color="#e2edf6")
    axis.tick_params(length=0, pad=5, labelsize=9, colors="#294968")
    axis.tick_params(axis="x", labelbottom=False)
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.bar_label(bars, labels=[f"{value:,}" for value in values], padding=4, color="#123d64", fontsize=9)
    output = BytesIO()
    canvas.print_png(output)
    encoded = b64encode(output.getvalue()).decode("ascii")
    alt = escape(", ".join(f"{label} {value}" for label, value in rows), quote=True)
    return f'<img class="chart" alt="{alt}" src="data:image/png;base64,{encoded}">'


def render_report(data: Mapping[str, object]) -> str:
    summary = fields(data.get("summary"))
    users, total = count(summary.get("user_count")), count(summary.get("trace_count"))
    understanding = count(fields(data.get("understanding_requests")).get("trace_count"))
    by_topic = {row.get("topic"): count(row.get("trace_count"))
                for raw in data["topics"] if isinstance((row := fields(raw)).get("topic"), str)}
    ranked = sorted(
        ((label, by_topic.get(key, 0)) for key, label in TOPICS.items() if by_topic.get(key, 0)),
        key=lambda row: -row[1],
    )
    population = fields(data.get("topic_population"))
    if population:
        eligible = count(population.get("eligible_trace_count"))
        excluded = count(population.get("excluded_trace_count"))
        unknown = count(population.get("unclassified_trace_count"))
        semantic = count(population.get("semantic_trace_count"))
        reasons = fields(population.get("excluded_by_reason"))
        social = count(reasons.get("feedback")) + count(reasons.get("chitchat"))
        exclusion_details = [
            f"{value:,} {label}" for value, label in (
                (count(reasons.get("access_and_identity")), "בנושאי גישה ואימות חשבון"),
                (social, "כמשוב או נימוס"),
                (count(reasons.get("empty_prompt")), "ללא טקסט"),
            ) if value
        ]
        exclusion_note = ""
        if exclusion_details:
            breakdown = " ו־".join(exclusion_details) if len(exclusion_details) <= 2 else ", ".join(exclusion_details)
            exclusion_note = f"מבין {excluded:,} ההודעות שהוחרגו, {breakdown}. "
        coverage_note = (f"בסיס הסיווג כולל {quantity(eligible, 'פנייה', 'פניות')}. "
                         f"ללא שיוך נותרו {unknown:,} מתוך {eligible:,}. "
                         f"{exclusion_note}הסיווג הסמנטי שייך לנושא {semantic:,} מהפניות שנבדקו.")
    else:
        coverage_note = (f"בפלט הניתוח הישן נרשמו {by_topic.get('unclassified', 0):,} פניות ללא שיוך. "
                         "נתוני החרגה ומקור הסיווג זמינים לאחר הרצה מחודשת של הניתוח.")
    overview = (f"בקשות להבנת בדיקות הופיעו ב־{understanding:,} מהפניות. "
                "הדוח מרכז את הנושאים, הצרכים ודפוסי ההמשך שנצפו בשיחות.")
    if ranked:
        topic, topic_count = ranked[0]
        overview = f"נושא בולט במדגם היה {topic}, עם {quantity(topic_count, 'פנייה', 'פניות')}. " + overview
    kpis = "".join(f'<div class="card"><small>{label}</small><b>{value:,}</b></div>'
                   for label, value in (("משתמשים מזוהים", users),
                                        ("שיחות מזוהות", count(summary.get("session_count"))),
                                        ("פניות שתועדו", total)))
    focuses = fields(data.get("state_finding_users_by_focus"))
    focus_keys = sorted(FOCUSES, key=lambda key: -count(focuses.get(key)))[:4]
    findings = "".join(progress(FOCUSES[key], count(focuses[key]), users, "משתמש", "משתמשים")
                       for key in focus_keys if count(focuses.get(key)))
    friction = fields(data.get("friction_metrics"))
    error_rate = friction.get("error_rate")
    if error_rate is None and "error_trace_count" in friction:
        error_rate = {"count": friction["error_trace_count"], "eligible_count": total}
    repeat_rate = friction.get("repeat_after_incomplete")
    pushback_rate = friction.get("doctor_referral_followup_pushback")
    friction_cards = "".join((
        rate_card("שגיאות ביצוע", "מכלל הפניות", error_rate),
        rate_card("שאלות חוזרות", "אחרי מענה חלקי", repeat_rate),
        rate_card("הפניה לרופא", "מהתשובות הלא ריקות", friction.get("doctor_referral_wording")),
        rate_card("הסתייגות", "אחרי הפניה לרופא", pushback_rate),
    ))
    friction_overview = "השווינו בין שגיאות ביצוע מתועדות לבין דפוסים בהודעות ההמשך."
    errors = count(fields(error_rate).get("count"))
    repeats = count(fields(repeat_rate).get("count"))
    error_summary = "תועדה שגיאת ביצוע אחת." if errors == 1 else f"תועדו {errors:,} שגיאות ביצוע."
    if format_rate(repeat_rate)[0] != "-":
        repeat_cases = "במקרה 1" if repeats == 1 else f"ב־{repeats:,} מקרים"
        friction_overview = f"{error_summary} {repeat_cases} זוהתה חזרה על שאלה אחרי מענה חלקי."
    elif errors:
        friction_overview = f"{error_summary} שיעור החזרה דורש תורים עם תגובת המשך."
    coverage = fields(data.get("request_coverage"))
    statuses = fields(coverage.get("by_status"))
    components = count(coverage.get("component_count"))
    covered = "".join(progress(label, count(statuses.get(key)), components, "רכיב", "רכיבים")
                      for key, label in COVERAGE.items() if count(statuses.get(key)))
    coverage_intro = "אין רכיבים זמינים לחישוב."
    if components:
        addressed = count(statuses.get("addressed"))
        coverage_intro = (f"זוהתה התייחסות מפורשת ל־{addressed:,} מתוך {components:,} רכיבי הבקשה. "
                          "הסיווג בודק את הכיסוי של כל רכיב בנפרד.")
    attention_percent, attention_note = attention_summary(fields(data.get("clinical_signals")))
    source = "מקור הנתונים · קובץ מקומי" if data.get("source") == "local_json" else "מקור הנתונים · Langfuse"
    template = Template(Path(__file__).with_name("report_template.html").read_text(encoding="utf-8"))
    return template.substitute(
        overview=escape(overview), kpis=kpis, chart=topic_chart(ranked), topic_population=escape(coverage_note),
        known_users=quantity(users, "משתמש", "משתמשים"),
        findings=findings or '<p class="note">אין ממצאים מזוהים לתצוגה.</p>',
        source=escape(source), friction_overview=escape(friction_overview), friction=friction_cards,
        coverage_intro=escape(coverage_intro),
        coverage=covered or '<p class="note">אין רכיבי בקשה מסווגים לתצוגה.</p>',
        attention_percent=attention_percent, attention_note=escape(attention_note),
    )


def find_browser() -> Path:
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge"):
        executable = shutil.which(name)
        if executable:
            return Path(executable)
    if os.name == "nt":
        for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if directory := os.environ.get(root):
                for name in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
                    path = Path(directory) / name
                    if path.is_file():
                        return path
    raise ReportError("Chromium, Chrome, or Microsoft Edge is required for PDF rendering")


def print_pdf(browser: Path, source: Path, output: Path) -> None:
    flags = [
        "--headless", "--disable-gpu", "--disable-background-networking", "--disable-dev-shm-usage",
        "--host-resolver-rules=MAP * ~NOTFOUND", "--no-pdf-header-footer",
        f"--user-data-dir={source.parent / 'browser-profile'}", f"--print-to-pdf={output}",
    ]
    # The container runs as a non-root user and only opens locally generated, network-disabled HTML.
    if os.environ.get("DOCKER_CONTAINER") == "1" or Path("/.dockerenv").is_file():
        flags.extend(("--no-sandbox", "--disable-setuid-sandbox"))
    subprocess.run([str(browser), *flags, source.as_uri()], check=True, timeout=90,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def write_pdf(analysis: Mapping[str, object], destination: Path) -> None:
    if destination.suffix.casefold() != ".pdf":
        raise ReportError("output path must end in .pdf")
    if not isinstance(analysis.get("summary"), Mapping) or not isinstance(analysis.get("topics"), list):
        raise ReportError("expected aggregate analysis JSON")
    try:
        browser = find_browser()
        html = render_report(analysis)
        destination = destination.resolve()
        copy_to_destination = False
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            workspace = TemporaryDirectory(dir=destination.parent, prefix=".report-", ignore_cleanup_errors=True)
        except PermissionError:
            workspace = TemporaryDirectory(prefix=".report-", ignore_cleanup_errors=True)
            copy_to_destination = True
        with workspace as folder:
            source = Path(folder) / "report.html"
            source.write_text(html, encoding="utf-8")
            temporary = source.with_suffix(".pdf")
            print_pdf(browser, source, temporary)
            with temporary.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise ReportError("browser produced an invalid PDF")
            try:
                if copy_to_destination:
                    shutil.copyfile(temporary, destination)
                else:
                    temporary.replace(destination)
            except PermissionError:
                raise ReportError("PDF destination is not writable by the current user") from None
    except ImportError:
        raise ReportError("install the Python PDF dependencies from requirements.txt") from None
    except (OSError, subprocess.SubprocessError):
        raise ReportError("could not render or write the PDF") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render aggregate analysis JSON to a Hebrew PDF.")
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("report.pdf"))
    args = parser.parse_args(argv)
    try:
        if args.input_json.resolve() == args.output.resolve():
            raise ReportError("analysis input and PDF destination must differ")
        analysis = fields(json.loads(args.input_json.read_text(encoding="utf-8-sig")))
        write_pdf(analysis, args.output)
    except ReportError as error:
        print(f"report: {error}", file=sys.stderr)
        return 5
    except (OSError, ValueError, RecursionError):
        print("report: could not read aggregate analysis JSON", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
