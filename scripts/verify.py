"""Audit the rows still flagged for manual verification.

    python3 scripts/verify.py              # report what the last refresh settles
    python3 scripts/verify.py --apply      # clear the flag on the settled rows
    python3 scripts/verify.py --markdown   # same report as markdown

Most VERIFY notes on seed-list.csv ask a question ClinicalTrials.gov can now
answer: is this drug really clinical-stage, what phase did it reach, is it
still running. The refresh already fetches that and stores it in the snapshot,
so this reads data/latest.json rather than hitting the API again.

Rows are sorted into three buckets:

  settled    ClinicalTrials.gov found trials and agrees with the seed phase.
             --apply rewrites the status from the derived one and drops the
             VERIFY note. Nothing else about the row is touched.

  ahead      ClinicalTrials.gov reports a HIGHER phase than the seed list.
             Never applied automatically: criteria.md is explicit that a row
             where the API is ahead gets flagged for a human rather than
             silently rewritten, because the API's phase can be the ceiling of
             a combined trial rather than the phase the drug actually reached.

  no trials  Nothing matched. Usually a drug registered outside
             ClinicalTrials.gov (CTR20 in China, JapicCTI in Japan) or one that
             never reached the clinic. Needs a human and a registry, so the
             report tells you which registry column to fill in.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import LATEST_JSON, ROOT, SEED_CSV, phase_rank, read_json  # noqa: E402

STATUS_WORDING = {
    "active": "active",
    "completed": "completed",
    "discontinued": "discontinued",
    "deprioritised": "deprioritised",
    "approved": "approved",
}


# A registry can say what phase a trial is and whether it is running. It
# cannot say what an undisclosed target is, whether a filing went in, or
# whether a company still backs a programme. A note asking one of those stays
# open however many trials the refresh found.
OFF_REGISTRY = ("target", "indication", "filing", "licens", "programme",
                "private", "partner", "approval")


def bucket(drug: dict) -> str:
    derived = drug.get("derived", {})
    note = (drug.get("seed_status") or "").lower()
    if any(word in note for word in OFF_REGISTRY):
        return "open_question"
    if not derived.get("trial_count"):
        return "no_trials"
    if derived.get("phase_ahead_of_seed"):
        return "ahead"
    if derived.get("status") == "unclear":
        return "status_unclear"
    return "settled"


def proposed_status(drug: dict) -> str:
    """What the status column should say once the question is answered."""
    derived = drug["derived"]
    word = STATUS_WORDING.get(derived["status"], derived["status"])
    trials = derived["trial_count"]
    active = derived.get("active_trials") or 0
    if word == "active":
        detail = f"{active} active trial{'' if active == 1 else 's'}" if active else "trials registered"
        return f"active: {detail} at {derived['phase']} (ClinicalTrials.gov, {trials} linked)"
    return f"{word}: {derived['phase']} reached, {trials} trial{'' if trials == 1 else 's'} linked (ClinicalTrials.gov)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit rows flagged for verification.")
    parser.add_argument("--apply", action="store_true",
                        help="rewrite the status of settled rows in seed-list.csv")
    parser.add_argument("--markdown", action="store_true", help="emit markdown")
    args = parser.parse_args()

    latest = read_json(LATEST_JSON)
    if not latest:
        print("no data/latest.json — run scripts/refresh.py first", file=sys.stderr)
        return 1

    flagged = [d for d in latest.get("drugs", []) if d.get("needs_verification")]
    buckets: dict[str, list] = {"settled": [], "ahead": [], "status_unclear": [],
                                "open_question": [], "no_trials": []}
    for drug in flagged:
        buckets[bucket(drug)].append(drug)

    bullet = "- " if args.markdown else "  "
    head = (lambda t: f"\n## {t}\n") if args.markdown else (lambda t: f"\n--- {t}\n")

    print(f"{len(flagged)} of {len(latest.get('drugs', []))} rows are flagged for verification "
          f"(snapshot {latest.get('snapshot_date')})")

    print(head(f"Settled by the refresh — {len(buckets['settled'])}"))
    for d in buckets["settled"]:
        print(f"{bullet}{d['drug_name']} ({d['company']}) — {proposed_status(d)}")
        print(f"{'  '}  was: {d['seed_status']}")

    print(head(f"ClinicalTrials.gov is ahead of the seed list — {len(buckets['ahead'])}"))
    for d in buckets["ahead"]:
        dv = d["derived"]
        print(f"{bullet}{d['drug_name']} ({d['company']}) — seed says {d['seed_phase'] or 'no phase'}, "
              f"ClinicalTrials.gov says {dv.get('ctgov_phase')} across {dv['trial_count']} trial(s)")
        for t in d.get("trials", [])[:3]:
            print(f"      {t['nct_id']} {t.get('phase') or '-'} {t.get('overall_status', '').lower()} — "
                  f"{(t.get('title') or '')[:70]}")

    print(head(f"Trials found but the status will not resolve — {len(buckets['status_unclear'])}"))
    for d in buckets["status_unclear"]:
        dv = d["derived"]
        print(f"{bullet}{d['drug_name']} ({d['company']}) — {dv['trial_count']} trial(s) at "
              f"{dv.get('ctgov_phase')}, none of them conclusive")
        for t in d.get("trials", [])[:3]:
            print(f"      {t['nct_id']} {(t.get('overall_status') or '').lower() or 'no status'}")

    print(head(f"Open question a registry cannot answer — {len(buckets['open_question'])}"))
    for d in buckets["open_question"]:
        dv = d["derived"]
        print(f"{bullet}{d['drug_name']} ({d['company']}) — {d['seed_status']}")
        print(f"      registry meanwhile: {dv['trial_count']} trial(s), "
              f"{dv.get('ctgov_phase') or 'no phase'}, {dv['status']}")

    print(head(f"No trials found — {len(buckets['no_trials'])}"))
    for d in buckets["no_trials"]:
        other = d.get("other_registry_ids") or ""
        hint = f"has registry ids: {other}" if other else "no registry id on the row either"
        print(f"{bullet}{d['drug_name']} ({d['company']}) — {hint}")
        print(f"      {d['seed_status']}")

    manual = sum(len(v) for k, v in buckets.items() if k != "settled")
    if not args.apply:
        print(f"\n{len(buckets['settled'])} row(s) can be cleared with --apply. "
              f"The other {manual} need a human.")
        return 0

    # --- rewrite only the settled rows, leaving every other column alone
    settled = {d["key"]: d for d in buckets["settled"]}
    with open(SEED_CSV, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        rows = list(reader)

    changed = 0
    for row in rows:
        key = next((k for k, d in settled.items() if d["drug_name"] == row["drug_name"]), None)
        if not key:
            continue
        row["status"] = proposed_status(settled[key])
        row["last_checked"] = latest.get("snapshot_date") or row["last_checked"]
        changed += 1

    with open(SEED_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nupdated {changed} row(s) in {os.path.relpath(SEED_CSV, ROOT)} — "
          f"re-run scripts/refresh.py and scripts/build.py to see them on the page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
