"""Print a markdown report of the last refresh.

    python3 scripts/report.py              # everything that changed
    python3 scripts/report.py --new-only   # only candidates first seen today

Used by the GitHub Action for the job summary and for the review issue.
Exit code 0 always; prints nothing but a heading when nothing changed.
"""

from __future__ import annotations

import argparse
import sys

from common import LATEST_JSON, REVIEW_QUEUE_JSON, read_json

GROUP_TITLES = {
    "drug_added": "New drugs on the seed list",
    "drug_removed": "Removed from the seed list",
    "phase_change": "Phase changes",
    "status_change": "Status changes",
    "trial_added": "Newly linked trials",
    "trial_status_change": "Trial status changes",
    "results_posted": "Results posted",
    "enrolment_change": "Enrolment changes",
    "candidate_flagged": "Flagged for manual review",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-only", action="store_true", help="only new review candidates")
    args = parser.parse_args()

    latest = read_json(LATEST_JSON)
    if not latest:
        print("No snapshot found — run scripts/refresh.py first.")
        return 0
    queue = read_json(REVIEW_QUEUE_JSON, {"candidates": []}) or {}
    date = latest.get("snapshot_date")
    new_candidates = [
        c for c in queue.get("candidates", []) if c.get("first_seen") == date
    ]

    if args.new_only:
        if not new_candidates:
            return 0
        print(f"### Possible new AI-designed drugs — {date}\n")
        print(
            "Interventional trials from tracked companies that are not linked to any drug "
            "in `seed-list.csv`. Nothing has been added automatically. Check each against "
            "`criteria.md`, then either add a row to `seed-list.csv` or record the verdict "
            "in `data/review-decisions.json`.\n"
        )
        for cand in new_candidates:
            print(
                f"- [{cand['nct_id']}]({cand['url']}) — **{cand.get('lead_sponsor')}** · "
                f"{cand.get('phase')} · {cand.get('overall_status', '').lower()} · "
                f"started {cand.get('start_date') or 'n/a'}  \n"
                f"  {cand.get('title')}  \n"
                f"  interventions: {', '.join(cand.get('interventions') or []) or 'n/a'}"
            )
        return 0

    stats = latest.get("stats", {})
    changes = (latest.get("changes_since_previous") or {}).get("changes", [])
    previous = (latest.get("changes_since_previous") or {}).get("previous_snapshot")

    print(f"## Refresh {date}\n")
    print(
        f"{stats.get('drugs', 0)} drugs · {stats.get('trials', 0)} linked trials · "
        f"{stats.get('candidates_pending', 0)} candidates pending review · "
        f"{latest.get('api_calls', 0)} API calls\n"
    )
    if not previous:
        print("Baseline snapshot — nothing to compare against yet.\n")
    elif not changes:
        print(f"No changes since {previous}.\n")
    else:
        print(f"{len(changes)} change(s) since {previous}.\n")
        for kind, title in GROUP_TITLES.items():
            rows = [c for c in changes if c["type"] == kind]
            if not rows:
                continue
            print(f"### {title} ({len(rows)})\n")
            for change in rows[:40]:
                drug = change.get("drug") or change.get("nct_id") or ""
                print(f"- **{drug}** — {change.get('detail')}")
            if len(rows) > 40:
                print(f"- … and {len(rows) - 40} more")
            print()

    warnings = latest.get("warnings") or []
    if warnings:
        print(f"### Warnings ({len(warnings)})\n")
        for warning in warnings[:20]:
            print(f"- {warning}")
        print()

    if new_candidates:
        print(f"### New review candidates ({len(new_candidates)})\n")
        for cand in new_candidates[:25]:
            print(
                f"- [{cand['nct_id']}]({cand['url']}) {cand.get('lead_sponsor')} · "
                f"{cand.get('phase')} · {cand.get('title')}"
            )
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
