"""Build the static dashboard payload from the stored snapshots.

    python3 scripts/build.py
    python3 scripts/build.py --include-review   # local working copy only

Writes site/data/dashboard.js (a `window.DASHBOARD = {...}` assignment, so the
page also works from a file:// URL) and site/data/dashboard.json (the same
payload, for anyone who wants the data rather than the page).

The manual-review queue is deliberately NOT in that payload. Its candidates are
unvetted trials that have not been checked against criteria.md, so publishing
them would put unverified AI-design claims about named companies on a public
page. --include-review writes them to site/data/review.js instead, which is
gitignored and therefore never reaches the deployed site; a plain build deletes
that file so a stale copy cannot linger. Both workflows deploy site/ as-is, so
"not committed" and "not built by default" is what keeps the queue private.

The page itself, site/index.html, is static: everything it draws comes from
this payload, so a refresh never has to touch the HTML.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys

from common import (
    BENCHMARKS_JSON,
    CATALYSTS_JSON,
    CATALYST_TYPES,
    CHANGELOG_JSON,
    CRITERIA_MD,
    FUNNEL_STAGES,
    LATEST_JSON,
    PHASE_SCALE,
    REVIEW_QUEUE_JSON,
    SITE_DATA_DIR,
    SNAPSHOT_DIR,
    months_between,
    now_utc_iso,
    parse_catalyst_date,
    read_json,
    write_json,
)

MAX_CHANGELOG_ENTRIES = 26  # about six months of weekly refreshes


# --------------------------------------------------------------------------
# a very small markdown subset, enough for criteria.md
# --------------------------------------------------------------------------


def inline_md(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        out,
    )
    return out


def markdown_section(markdown: str, heading: str) -> str:
    """Return the HTML for one `## heading` section of a markdown file."""
    lines = markdown.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("## ") and heading.lower() in line.lower():
            start = index + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        if line.strip().startswith("## "):
            break
        body.append(line)
    return render_md("\n".join(body).strip())


def render_md(markdown: str) -> str:
    out: list[str] = []
    buffer: list[str] = []
    mode: str | None = None

    def flush() -> None:
        nonlocal mode, buffer
        if not buffer:
            mode = None
            return
        if mode == "ul":
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in buffer) + "</ul>")
        elif mode == "ol":
            out.append("<ol>" + "".join(f"<li>{item}</li>" for item in buffer) + "</ol>")
        elif mode == "table":
            header, *rows = buffer
            head = "".join(f"<th>{cell}</th>" for cell in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
            )
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        elif mode == "p":
            out.append(f"<p>{' '.join(buffer)}</p>")
        buffer = []
        mode = None

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if re.match(r"^\s*\|.*\|\s*$", line):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
                continue  # the |---|---| separator row
            if mode != "table":
                flush()
                mode = "table"
            buffer.append([inline_md(cell) for cell in cells])
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        number = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if bullet or number:
            want = "ul" if bullet else "ol"
            if mode != want:
                flush()
                mode = want
            buffer.append(inline_md((bullet or number).group(1)))
            continue
        if mode not in (None, "p"):
            flush()
        mode = "p"
        buffer.append(inline_md(line.strip()))
    flush()
    return "".join(out)


# --------------------------------------------------------------------------
# payload
# --------------------------------------------------------------------------


def compact_trial(trial: dict) -> dict:
    keep = (
        "nct_id",
        "title",
        "overall_status",
        "why_stopped",
        "phases",
        "enrollment",
        "enrollment_type",
        "start_date",
        "primary_completion_date",
        "completion_date",
        "last_update_posted",
        "has_results",
        "lead_sponsor",
        "conditions",
        "countries",
        "url",
        "match_reason",
    )
    return {field: trial.get(field) for field in keep}


def compact_drug(drug: dict) -> dict:
    keep = (
        "key",
        "drug_name",
        "alt_codes",
        "company",
        "company_clean",
        "partner",
        "ai_platform",
        "modality",
        "modality_group",
        "target",
        "indication",
        "therapy_area",
        "therapy_area_group",
        "seed_phase",
        "seed_status",
        "first_in_human_date",
        "ai_role",
        "ai_role_group",
        "confidence",
        "source_url",
        "last_checked",
        "needs_verification",
        "other_registry_ids",
        "stale_from",
    )
    out = {field: drug.get(field) for field in keep}
    out["derived"] = drug.get("derived", {})
    out["trials"] = [compact_trial(t) for t in drug.get("trials", [])]
    return out


def build_catalysts(raw: dict, drugs: list[dict], as_of: str) -> tuple[list[dict], list[dict], list[str]]:
    """Resolve the hand-maintained catalyst file against the tracked drugs.

    Returns (curated, derived, warnings). Curated entries are the whole point —
    a human wrote them. Derived entries are the next primary completion date
    ClinicalTrials.gov holds for an active drug, so the strip keeps saying
    something useful between manual updates.
    """
    warnings: list[str] = []
    by_key = {drug["key"]: drug for drug in drugs}
    horizon = int(raw.get("horizon_months") or 18)
    curated: list[dict] = []

    for index, entry in enumerate(raw.get("catalysts", [])):
        window = parse_catalyst_date(entry.get("date", ""))
        if not window:
            warnings.append(
                f"catalysts.json[{index}]: cannot read date {entry.get('date')!r} — skipped"
            )
            continue
        drug = None
        if entry.get("drug_key"):
            drug = by_key.get(entry["drug_key"])
            if not drug:
                warnings.append(
                    f"catalysts.json[{index}]: drug_key {entry['drug_key']!r} is not in seed-list.csv"
                )
        subject = drug["drug_name"] if drug else entry.get("subject") or "(unnamed)"
        months_away = months_between(as_of, window["start"])
        if months_away > horizon:
            continue  # beyond the horizon the file asked for
        overdue = window["end"] < as_of
        if overdue and months_between(window["end"], as_of) > 12:
            continue  # a year late and still nothing: stop showing it
        curated.append(
            {
                "source": "curated",
                "subject": subject,
                "drug_key": drug["key"] if drug else None,
                "company": drug["company"] if drug else entry.get("company"),
                "phase": (drug.get("derived") or {}).get("phase") if drug else None,
                "tracked": bool(drug),
                "type": entry.get("type", "other"),
                "type_label": CATALYST_TYPES.get(entry.get("type", "other"), "Milestone"),
                "title": entry.get("title") or CATALYST_TYPES.get(entry.get("type", "other")),
                "detail": entry.get("detail", ""),
                "date": entry.get("date"),
                "date_label": window["label"],
                "granularity": window["granularity"],
                "start": window["start"],
                "end": window["end"],
                "months_away": max(0, months_away),
                "overdue": overdue,
                # Already inside the guided window, but not yet past it.
                "window_open": not overdue and window["start"] <= as_of <= window["end"],
                "source_note": entry.get("source_note"),
                "source_url": entry.get("source_url"),
            }
        )
    # What is coming leads the strip; what has already slipped trails it, most
    # recently missed first — it is a caveat, not the headline.
    upcoming = sorted(
        (item for item in curated if not item["overdue"]),
        key=lambda item: (item["start"], item["end"], item["subject"]),
    )
    late = sorted(
        (item for item in curated if item["overdue"]),
        key=lambda item: (item["start"], item["subject"]),
        reverse=True,
    )
    curated = upcoming + late

    # Auto: the nearest primary completion still ahead of us, per active drug.
    derived: list[dict] = []
    for drug in drugs:
        if (drug.get("derived") or {}).get("status") not in ("active", "approved"):
            continue
        upcoming = []
        for trial in drug.get("trials", []):
            window = parse_catalyst_date(trial.get("primary_completion_date") or "")
            if not window or window["end"] < as_of:
                continue
            if months_between(as_of, window["start"]) > horizon:
                continue
            upcoming.append((window, trial))
        if not upcoming:
            continue
        window, trial = min(upcoming, key=lambda pair: pair[0]["start"])
        derived.append(
            {
                "source": "ctgov",
                "subject": drug["drug_name"],
                "drug_key": drug["key"],
                "company": drug["company"],
                "phase": (drug.get("derived") or {}).get("phase"),
                "type": "readout",
                "type_label": "Primary completion due",
                "date_label": window["label"],
                "start": window["start"],
                "nct_id": trial.get("nct_id"),
                "url": trial.get("url"),
                "trial_phase": (trial.get("phases") or []),
            }
        )
    derived.sort(key=lambda item: (item["start"], item["subject"]))
    return curated, derived, warnings


def load_history() -> list[dict]:
    """One compact row per snapshot: enough to draw phase counts over time."""
    history: list[dict] = []
    if not os.path.isdir(SNAPSHOT_DIR):
        return history
    for name in sorted(f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json")):
        snapshot = read_json(os.path.join(SNAPSHOT_DIR, name))
        if not snapshot:
            continue
        counts: dict[str, int] = {}
        for drug in snapshot.get("drugs", []):
            bucket = (drug.get("derived") or {}).get("phase_bucket")
            if bucket:
                counts[bucket] = counts.get(bucket, 0) + 1
        history.append(
            {
                "date": snapshot.get("snapshot_date"),
                "drugs": len(snapshot.get("drugs", [])),
                "trials": (snapshot.get("stats") or {}).get("trials"),
                "stage_counts": counts,
                "change_count": (snapshot.get("changes_since_previous") or {}).get("count", 0),
            }
        )
    return history


def write_review_file(queue: dict, include: bool) -> None:
    """Write (or clear) the local-only review queue beside the payload."""
    path = os.path.join(SITE_DATA_DIR, "review.js")
    if not include:
        if os.path.exists(path):
            os.remove(path)
            print("removed site/data/review.js (use --include-review to keep it)", file=sys.stderr)
        return
    os.makedirs(SITE_DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("// Generated by scripts/build.py --include-review — do not edit, do not commit.\n")
        fh.write("// Unvetted review candidates. Gitignored so they stay off the published site.\n")
        fh.write("window.REVIEW_QUEUE = ")
        json.dump(
            {
                "generated": queue.get("generated"),
                "note": queue.get("note"),
                "candidates": queue.get("candidates", []),
            },
            fh,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        fh.write(";\n")
    print(
        f"built site/data/review.js — {len(queue.get('candidates', []))} candidates, local only",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the static dashboard payload.")
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="also write site/data/review.js so the manual-review table shows up "
        "when you open the page locally (gitignored, never published)",
    )
    args = parser.parse_args()

    latest = read_json(LATEST_JSON)
    if not latest:
        print("no data/latest.json — run scripts/refresh.py first", file=sys.stderr)
        return 1

    changelog = read_json(CHANGELOG_JSON, []) or []
    benchmarks = read_json(BENCHMARKS_JSON, {}) or {}
    catalyst_file = read_json(CATALYSTS_JSON, {}) or {}
    queue = read_json(REVIEW_QUEUE_JSON, {"candidates": []}) or {}

    with open(CRITERIA_MD, "r", encoding="utf-8") as fh:
        criteria_md = fh.read()

    drugs = [compact_drug(d) for d in latest.get("drugs", [])]
    catalysts, derived_catalysts, catalyst_warnings = build_catalysts(
        catalyst_file, drugs, latest.get("snapshot_date") or ""
    )
    for warning in catalyst_warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    history = load_history()
    changes = (latest.get("changes_since_previous") or {}).get("changes", [])

    payload = {
        "meta": {
            "generated_at_utc": now_utc_iso(),
            "last_refresh": latest.get("snapshot_date"),
            "previous_refresh": (latest.get("changes_since_previous") or {}).get(
                "previous_snapshot"
            ),
            "refresh_mode": latest.get("mode"),
            "snapshot_count": len(history),
            "seed_file": latest.get("seed_file"),
            "seed_sha256": latest.get("seed_sha256"),
            "api_calls": latest.get("api_calls"),
            "sponsors_swept": latest.get("sponsors_swept", []),
            "sponsors_skipped": latest.get("sponsors_skipped", []),
            "sweep_settings": latest.get("sweep_settings", {}),
            "warnings": latest.get("warnings", []),
            "source": latest.get("source", {}),
            "phase_scale": PHASE_SCALE,
            "funnel_stages": FUNNEL_STAGES,
        },
        "criteria": {
            "inclusion_html": markdown_section(criteria_md, "What counts as"),
            "confidence_html": markdown_section(criteria_md, "Confidence column"),
            "gaps_html": markdown_section(criteria_md, "Known gaps"),
            "watchlist_html": markdown_section(criteria_md, "Watchlist"),
            "sources_html": markdown_section(criteria_md, "Sources used"),
            "file": os.path.basename(CRITERIA_MD),
        },
        "benchmarks": benchmarks,
        "catalysts": {
            "last_reviewed": catalyst_file.get("last_reviewed"),
            "horizon_months": catalyst_file.get("horizon_months", 18),
            "file": os.path.basename(CATALYSTS_JSON),
            "warnings": catalyst_warnings,
            "curated": catalysts,
            "derived": derived_catalysts,
        },
        "drugs": drugs,
        "history": history,
        "changes_since_last_refresh": changes,
        "changelog": [
            {
                "date": entry.get("date"),
                "previous_snapshot": entry.get("previous_snapshot"),
                "change_count": entry.get("change_count", 0),
                "totals": entry.get("totals", {}),
                "changes": entry.get("changes", []),
                "warning_count": len(entry.get("warnings", []) or []),
            }
            for entry in changelog[:MAX_CHANGELOG_ENTRIES]
        ],
        # review_queue is intentionally absent — see the module docstring.
    }

    write_review_file(queue, args.include_review)
    write_json(os.path.join(SITE_DATA_DIR, "dashboard.json"), payload, indent=1)
    js_path = os.path.join(SITE_DATA_DIR, "dashboard.js")
    os.makedirs(SITE_DATA_DIR, exist_ok=True)
    with open(js_path, "w", encoding="utf-8") as fh:
        fh.write("// Generated by scripts/build.py — do not edit.\n")
        fh.write("window.DASHBOARD = ")
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    size = os.path.getsize(js_path) / 1024
    print(
        f"built site/data/dashboard.js ({size:.0f} KB) — {len(drugs)} drugs, "
        f"{sum(len(d['trials']) for d in drugs)} trial links, "
        f"{len(history)} snapshots, {len(catalysts)} catalysts "
        f"(+{len(derived_catalysts)} derived), {len(changes)} changes since last refresh",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
