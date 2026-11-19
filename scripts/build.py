"""Build the static dashboard payload from the stored snapshots.

    python3 scripts/build.py

Writes site/data/dashboard.js (a `window.DASHBOARD = {...}` assignment, so the
page also works from a file:// URL) and site/data/dashboard.json (the same
payload, for anyone who wants the data rather than the page).

The page itself, site/index.html, is static: everything it draws comes from
this payload, so a refresh never has to touch the HTML.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys

from common import (
    BENCHMARKS_JSON,
    CHANGELOG_JSON,
    CRITERIA_MD,
    FUNNEL_STAGES,
    LATEST_JSON,
    PHASE_SCALE,
    REVIEW_QUEUE_JSON,
    SITE_DATA_DIR,
    SNAPSHOT_DIR,
    now_utc_iso,
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


def main() -> int:
    latest = read_json(LATEST_JSON)
    if not latest:
        print("no data/latest.json — run scripts/refresh.py first", file=sys.stderr)
        return 1

    changelog = read_json(CHANGELOG_JSON, []) or []
    benchmarks = read_json(BENCHMARKS_JSON, {}) or {}
    queue = read_json(REVIEW_QUEUE_JSON, {"candidates": []}) or {}

    with open(CRITERIA_MD, "r", encoding="utf-8") as fh:
        criteria_md = fh.read()

    drugs = [compact_drug(d) for d in latest.get("drugs", [])]
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
        "review_queue": {
            "generated": queue.get("generated"),
            "note": queue.get("note"),
            "candidates": queue.get("candidates", []),
        },
    }

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
        f"{len(history)} snapshots, {len(changes)} changes since last refresh",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
