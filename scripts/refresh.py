"""Refresh trial data for every drug in seed-list.csv and write a dated snapshot.

    python3 scripts/refresh.py            # full refresh against ClinicalTrials.gov
    python3 scripts/refresh.py --limit 5  # quick smoke test
    python3 scripts/refresh.py --offline  # re-derive a snapshot from cached trials

What it does, per drug:
  1. searches ClinicalTrials.gov v2 by drug code / INN to find trials,
  2. adds the NCT IDs already curated in the seed list,
  3. sweeps every tracked sponsor for interventional trials, attaching any that
     match a tracked drug and parking the rest in the manual review queue,
  4. derives highest phase / status / enrolment, and
  5. diffs against the previous snapshot to produce the changelog.

Nothing is ever added to the seed list automatically: discovery only ever
writes to data/review-queue.json.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

from common import (
    CHANGELOG_JSON,
    LATEST_JSON,
    OVERRIDES_JSON,
    REVIEW_DECISIONS_JSON,
    REVIEW_QUEUE_JSON,
    SEED_CSV,
    SNAPSHOT_DIR,
    SPONSOR_ALIASES_JSON,
    CTG_STATUS_CLASS,
    FUNNEL_BUCKET,
    ctg_phase,
    load_seed,
    now_utc_iso,
    phase_rank,
    read_json,
    search_tokens,
    squash,
    today_iso,
    write_json,
)
from ctgov import Client, CtgError, flatten

MAX_DISCOVERED_PER_DRUG = 50

# A sponsor sweep is for AI-native companies, where a new trial is a real
# signal. Big-pharma partners run hundreds of unrelated trials, so sweeping
# them would bury the review queue; their AI assets are still tracked by drug
# code through the per-drug search above.
SPONSOR_TRIAL_CAP = 80

# Only flag recent trials for review — an untracked 2014 trial is not news.
CANDIDATE_LOOKBACK_YEARS = 3


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------


def haystacks(trial: dict) -> tuple[str, str]:
    """(squashed, lowercased) text a drug code could appear in."""
    parts = [
        trial.get("title") or "",
        trial.get("org_study_id") or "",
        " ".join(trial.get("secondary_ids") or []),
        " ".join(trial.get("interventions") or []),
    ]
    text = " ".join(parts)
    return squash(text), text.lower()


def sponsor_matches(drug: dict, trial: dict, aliases: dict) -> bool:
    sponsor_text = squash(
        " ".join([trial.get("lead_sponsor") or ""] + (trial.get("collaborators") or []))
    )
    if not sponsor_text:
        return False
    names = [drug["company_clean"], drug["partner_clean"]]
    names += aliases.get(drug["company_clean"], [])
    for name in names:
        needle = squash(name)
        if len(needle) >= 5 and needle in sponsor_text:
            return True
    return False


def match_reason(drug: dict, tokens: list[dict], trial: dict, aliases: dict) -> str | None:
    """Why (if at all) this trial belongs to this drug."""
    squashed, lowered = haystacks(trial)
    for token in tokens:
        if token["kind"] == "code" and token["squashed"] in squashed:
            return f"code:{token['text']}"
    for token in tokens:
        if token["kind"] != "word":
            continue
        pattern = r"\b" + r"\s*".join(re.escape(ch) for ch in token["text"].lower().split())
        if re.search(pattern, lowered) and sponsor_matches(drug, trial, aliases):
            return f"name+sponsor:{token['text']}"
    return None


# --------------------------------------------------------------------------
# derivation
# --------------------------------------------------------------------------


def trial_sort_key(trial: dict) -> tuple:
    return (-phase_rank(ctg_phase(trial.get("phases")) or ""), trial.get("start_date") or "")


def derive(drug: dict, trials: list[dict]) -> dict:
    """Roll a drug's trials up into the numbers the dashboard charts."""
    ctg_phases = [ctg_phase(t.get("phases")) for t in trials]
    ctg_phases = [p for p in ctg_phases if p]
    ctg_best = max(ctg_phases, key=phase_rank, default=None)

    seed_phase = drug["seed_phase"] or None
    if seed_phase and phase_rank(seed_phase) >= phase_rank(ctg_best):
        phase, basis = seed_phase, "seed" if not ctg_best else "both"
    elif ctg_best:
        phase, basis = ctg_best, "ctgov"
    else:
        phase, basis = seed_phase, "seed"

    statuses = [CTG_STATUS_CLASS.get(t.get("overall_status") or "", "other") for t in trials]
    seed_class = drug["status_class"]
    status, resolved = seed_class, False
    if seed_class in ("unclear", "active", "completed") and statuses:
        if "active" in statuses:
            status = "active"
        elif statuses and all(s in ("completed", "stopped", "unknown") for s in statuses):
            status = "completed" if "completed" in statuses else seed_class
        resolved = status != seed_class

    enrolments = [t["enrollment"] for t in trials if isinstance(t.get("enrollment"), int)]
    starts = [t["start_date"] for t in trials if t.get("start_date")]
    completions = [
        t["primary_completion_date"] for t in trials if t.get("primary_completion_date")
    ]

    # Earliest trial start at or above each funnel stage: the phase timeline.
    stage_dates: dict[str, str] = {}
    for trial in trials:
        mapped = ctg_phase(trial.get("phases"))
        start = trial.get("start_date")
        if not mapped or not start:
            continue
        bucket = FUNNEL_BUCKET.get(mapped)
        if not bucket:
            continue
        if bucket not in stage_dates or start < stage_dates[bucket]:
            stage_dates[bucket] = start

    return {
        "phase": phase,
        "phase_basis": basis,
        "phase_bucket": FUNNEL_BUCKET.get(phase or "", None),
        "ctgov_phase": ctg_best,
        "phase_ahead_of_seed": bool(
            ctg_best and seed_phase and phase_rank(ctg_best) > phase_rank(seed_phase)
        ),
        "status": status,
        "status_resolved_from_ctgov": resolved,
        "trial_count": len(trials),
        "active_trials": sum(1 for s in statuses if s == "active"),
        "total_enrolment": sum(enrolments) if enrolments else None,
        "first_trial_start": min(starts) if starts else None,
        "latest_trial_start": max(starts) if starts else None,
        "next_primary_completion": min(
            (c for c in completions if c >= today_iso()[:7]), default=None
        ),
        "has_results": any(t.get("has_results") for t in trials),
        "last_update_posted": max(
            (t["last_update_posted"] for t in trials if t.get("last_update_posted")),
            default=None,
        ),
        "stage_first_start": stage_dates,
    }


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------


def previous_snapshot(exclude_date: str) -> dict | None:
    if not os.path.isdir(SNAPSHOT_DIR):
        return None
    files = sorted(f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json"))
    for name in reversed(files):
        if name[:-5] == exclude_date:
            continue
        return read_json(os.path.join(SNAPSHOT_DIR, name))
    return None


def diff_snapshots(prev: dict | None, curr: dict) -> list[dict]:
    """Human-readable changes between two refreshes."""
    changes: list[dict] = []
    if not prev:
        return changes

    prev_drugs = {d["key"]: d for d in prev.get("drugs", [])}
    curr_drugs = {d["key"]: d for d in curr.get("drugs", [])}

    for key, drug in curr_drugs.items():
        if key not in prev_drugs:
            changes.append(
                {
                    "type": "drug_added",
                    "drug": drug["drug_name"],
                    "key": key,
                    "company": drug["company"],
                    "detail": f"added to the seed list at {drug['derived']['phase']}",
                }
            )
            continue
        before, after = prev_drugs[key], drug
        if before["derived"]["phase"] != after["derived"]["phase"]:
            changes.append(
                {
                    "type": "phase_change",
                    "drug": after["drug_name"],
                    "key": key,
                    "company": after["company"],
                    "from": before["derived"]["phase"],
                    "to": after["derived"]["phase"],
                    "direction": "up"
                    if phase_rank(after["derived"]["phase"])
                    > phase_rank(before["derived"]["phase"])
                    else "down",
                    "detail": f"{before['derived']['phase']} -> {after['derived']['phase']}",
                }
            )
        if before["derived"]["status"] != after["derived"]["status"]:
            changes.append(
                {
                    "type": "status_change",
                    "drug": after["drug_name"],
                    "key": key,
                    "company": after["company"],
                    "from": before["derived"]["status"],
                    "to": after["derived"]["status"],
                    "detail": f"{before['derived']['status']} -> {after['derived']['status']}",
                }
            )

        prev_trials = {t["nct_id"]: t for t in before.get("trials", [])}
        for trial in after.get("trials", []):
            old = prev_trials.get(trial["nct_id"])
            if not old:
                changes.append(
                    {
                        "type": "trial_added",
                        "drug": after["drug_name"],
                        "key": key,
                        "company": after["company"],
                        "nct_id": trial["nct_id"],
                        "detail": f"{trial['nct_id']} linked ({trial.get('overall_status')})",
                    }
                )
                continue
            if old.get("overall_status") != trial.get("overall_status"):
                changes.append(
                    {
                        "type": "trial_status_change",
                        "drug": after["drug_name"],
                        "key": key,
                        "company": after["company"],
                        "nct_id": trial["nct_id"],
                        "from": old.get("overall_status"),
                        "to": trial.get("overall_status"),
                        "detail": f"{trial['nct_id']}: {old.get('overall_status')} -> {trial.get('overall_status')}",
                    }
                )
            if not old.get("has_results") and trial.get("has_results"):
                changes.append(
                    {
                        "type": "results_posted",
                        "drug": after["drug_name"],
                        "key": key,
                        "company": after["company"],
                        "nct_id": trial["nct_id"],
                        "detail": f"results posted for {trial['nct_id']}",
                    }
                )
            if (
                isinstance(old.get("enrollment"), int)
                and isinstance(trial.get("enrollment"), int)
                and old["enrollment"] != trial["enrollment"]
            ):
                changes.append(
                    {
                        "type": "enrolment_change",
                        "drug": after["drug_name"],
                        "key": key,
                        "company": after["company"],
                        "nct_id": trial["nct_id"],
                        "from": old["enrollment"],
                        "to": trial["enrollment"],
                        "detail": f"{trial['nct_id']}: enrolment {old['enrollment']} -> {trial['enrollment']}",
                    }
                )

    for key, drug in prev_drugs.items():
        if key not in curr_drugs:
            changes.append(
                {
                    "type": "drug_removed",
                    "drug": drug["drug_name"],
                    "key": key,
                    "company": drug["company"],
                    "detail": "removed from the seed list",
                }
            )

    prev_candidates = {c["nct_id"] for c in prev.get("candidates", [])}
    for cand in curr.get("candidates", []):
        if cand["nct_id"] not in prev_candidates:
            changes.append(
                {
                    "type": "candidate_flagged",
                    "drug": cand.get("title", "")[:90],
                    "key": None,
                    "company": cand.get("lead_sponsor"),
                    "nct_id": cand["nct_id"],
                    "detail": f"new trial from a tracked sponsor — needs manual review",
                }
            )
    return changes


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def file_sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


def run(limit: int | None, offline: bool, skip_sponsors: bool) -> dict:
    date = today_iso()
    seed = load_seed()
    if limit:
        seed = seed[:limit]
    overrides = read_json(OVERRIDES_JSON, {}) or {}
    decisions = {
        nct: value
        for nct, value in (read_json(REVIEW_DECISIONS_JSON, {}) or {}).items()
        if not nct.startswith("_") and isinstance(value, dict)
    }
    aliases = read_json(SPONSOR_ALIASES_JSON, {}) or {}
    prev = previous_snapshot(exclude_date=date)
    prev_drugs = {d["key"]: d for d in (prev or {}).get("drugs", [])}

    client = Client()
    warnings: list[str] = []
    trials_by_nct: dict[str, dict] = {}
    matches: dict[str, dict[str, str]] = {d["key"]: {} for d in seed}  # key -> nct -> reason

    # Cached trials let --offline re-derive without touching the network. The
    # cache is the newest snapshot there is, today's included; `prev` stays the
    # newest *earlier* one, because that is what the changelog diffs against.
    cache = read_json(LATEST_JSON) if offline else None
    if offline and cache:
        for drug in cache.get("drugs", []):
            for trial in drug.get("trials", []):
                trials_by_nct[trial["nct_id"]] = trial
            for nct, reason in (
                (t["nct_id"], t.get("match_reason", "cached")) for t in drug.get("trials", [])
            ):
                if drug["key"] in matches:
                    matches[drug["key"]][nct] = reason

    def register(drug: dict, trial: dict, reason: str) -> None:
        nct = trial.get("nct_id")
        if not nct:
            return
        over = overrides.get(drug["key"], {})
        if nct in (over.get("nct_exclude") or []):
            return
        trials_by_nct[nct] = trial
        matches[drug["key"]].setdefault(nct, reason)

    # 1. per-drug discovery -------------------------------------------------
    for index, drug in enumerate(seed, start=1):
        over = overrides.get(drug["key"], {})
        tokens = search_tokens(drug)
        print(f"[{index}/{len(seed)}] {drug['drug_name']}", file=sys.stderr)

        if not offline and over.get("discovery", "on") != "off" and tokens:
            expression = " OR ".join(f'"{t["text"]}"' for t in tokens)
            try:
                found = [flatten(s) for s in client.search_intervention(f"({expression})")]
            except CtgError as exc:
                warnings.append(f"{drug['drug_name']}: discovery failed ({exc})")
                found = []
            accepted = [
                (trial, reason)
                for trial in found
                if (reason := match_reason(drug, tokens, trial, aliases))
            ]
            accepted.sort(key=lambda pair: pair[0].get("last_update_posted") or "", reverse=True)
            if len(accepted) > MAX_DISCOVERED_PER_DRUG:
                warnings.append(
                    f"{drug['drug_name']}: {len(accepted)} discovered trials, kept the "
                    f"{MAX_DISCOVERED_PER_DRUG} most recently updated"
                )
                accepted = accepted[:MAX_DISCOVERED_PER_DRUG]
            for trial, reason in accepted:
                register(drug, trial, reason)

        # 2. curated + pinned IDs always count, even if search misses them.
        pinned = sorted(set(drug["seed_nct_ids"]) | set(over.get("nct_include") or []))
        missing = [n for n in pinned if n not in trials_by_nct]
        if missing and not offline:
            try:
                for study in client.studies_by_id(missing):
                    trial = flatten(study)
                    trials_by_nct[trial["nct_id"]] = trial
            except CtgError as exc:
                warnings.append(f"{drug['drug_name']}: id fetch failed ({exc})")
        for nct in pinned:
            trial = trials_by_nct.get(nct)
            if trial:
                register(drug, trial, "seed-list")
            else:
                warnings.append(f"{drug['drug_name']}: {nct} could not be fetched")

    # 3. sponsor sweep ------------------------------------------------------
    candidates: list[dict] = []
    candidate_ids: set[str] = set()
    swept_sponsors: list[str] = []
    skipped_sponsors: list[dict] = []
    if not offline and not skip_sponsors:
        cutoff = f"{int(date[:4]) - CANDIDATE_LOOKBACK_YEARS}{date[4:]}"
        sponsors = sorted(
            {d["company_clean"] for d in seed if len(squash(d["company_clean"])) >= 5}
        )
        tracked = {nct for per_drug in matches.values() for nct in per_drug}
        for index, sponsor in enumerate(sponsors, start=1):
            print(f"[sponsor {index}/{len(sponsors)}] {sponsor}", file=sys.stderr)
            try:
                total = client.count_sponsor(sponsor)
                if total > SPONSOR_TRIAL_CAP:
                    skipped_sponsors.append({"sponsor": sponsor, "trials": total})
                    continue
                studies = [flatten(s) for s in client.search_sponsor(sponsor)]
            except CtgError as exc:
                warnings.append(f"sponsor sweep failed for {sponsor}: {exc}")
                continue
            swept_sponsors.append(sponsor)
            needle = squash(sponsor)
            for trial in studies:
                sponsor_text = squash(
                    " ".join([trial.get("lead_sponsor") or ""] + (trial.get("collaborators") or []))
                )
                if needle not in sponsor_text:
                    continue  # the sponsor search is fuzzy; require a real match
                attached = False
                for drug in seed:
                    if drug["company_clean"] != sponsor and drug["partner_clean"] != sponsor:
                        continue
                    reason = match_reason(drug, search_tokens(drug), trial, aliases)
                    if reason:
                        register(drug, trial, f"sponsor-sweep/{reason}")
                        attached = True
                if attached or trial["nct_id"] in tracked or trial["nct_id"] in candidate_ids:
                    continue
                if not ctg_phase(trial.get("phases")):
                    continue  # no phase = not a drug trial we can place
                recent = max(
                    trial.get("start_date") or "", trial.get("last_update_posted") or ""
                )
                if recent < cutoff:
                    continue  # an untracked 2014 trial is not news
                decision = decisions.get(trial["nct_id"], {})
                if decision.get("decision") in ("rejected", "accepted"):
                    continue
                candidate_ids.add(trial["nct_id"])
                candidates.append(
                    {
                        "nct_id": trial["nct_id"],
                        "title": trial.get("title"),
                        "lead_sponsor": trial.get("lead_sponsor"),
                        "matched_sponsor": sponsor,
                        "phase": ctg_phase(trial.get("phases")),
                        "overall_status": trial.get("overall_status"),
                        "start_date": trial.get("start_date"),
                        "conditions": trial.get("conditions", [])[:4],
                        "interventions": trial.get("interventions", [])[:4],
                        "url": trial.get("url"),
                        "first_seen": date,
                        "review_status": "pending",
                    }
                )
    if offline and cache:
        candidates = cache.get("candidates", [])

    # A trial attached to any drug is tracked, not a candidate.
    attached_ids = {nct for per_drug in matches.values() for nct in per_drug}
    candidates = [c for c in candidates if c["nct_id"] not in attached_ids]
    candidates.sort(key=lambda c: (c.get("start_date") or "", c["nct_id"]), reverse=True)

    # 4. assemble -----------------------------------------------------------
    drugs: list[dict] = []
    for drug in seed:
        per_drug = matches[drug["key"]]
        trials = [dict(trials_by_nct[n], match_reason=r) for n, r in per_drug.items()]
        trials.sort(key=trial_sort_key)
        record = dict(drug)
        stale_from = None
        if not trials and drug["key"] in prev_drugs and prev_drugs[drug["key"]].get("trials"):
            # Never wipe good data because one API call failed.
            previous = prev_drugs[drug["key"]]
            trials = previous["trials"]
            stale_from = previous.get("stale_from") or prev.get("snapshot_date")
            warnings.append(f"{drug['drug_name']}: kept trials from {stale_from}")
        record["trials"] = trials
        record["stale_from"] = stale_from
        record["derived"] = derive(drug, trials)
        drugs.append(record)

    # Learn sponsor spellings, so next refresh matches "Gasherbrum Bio ...
    # subsidiary of Structure Therapeutics" without being told.
    for drug in drugs:
        for trial in drug["trials"]:
            sponsor = trial.get("lead_sponsor")
            if not sponsor or squash(drug["company_clean"]) in squash(sponsor):
                continue
            known = aliases.setdefault(drug["company_clean"], [])
            if sponsor not in known:
                known.append(sponsor)

    snapshot = {
        "snapshot_date": date,
        "generated_at_utc": now_utc_iso(),
        "source": {
            "api": "ClinicalTrials.gov API v2",
            "endpoint": "https://clinicaltrials.gov/api/v2/studies",
        },
        "seed_file": os.path.basename(SEED_CSV),
        "seed_sha256": file_sha256(SEED_CSV),
        "mode": "offline" if offline else "live",
        "api_calls": client.calls,
        "sponsors_swept": swept_sponsors,
        "sponsors_skipped": skipped_sponsors,
        "sweep_settings": {
            "sponsor_trial_cap": SPONSOR_TRIAL_CAP,
            "candidate_lookback_years": CANDIDATE_LOOKBACK_YEARS,
        },
        "warnings": warnings,
        "drugs": drugs,
        "candidates": candidates,
    }
    snapshot["stats"] = {
        "drugs": len(drugs),
        "trials": len({t["nct_id"] for d in drugs for t in d["trials"]}),
        "drugs_with_trials": sum(1 for d in drugs if d["trials"]),
        "candidates_pending": len(candidates),
    }

    changes = diff_snapshots(prev, snapshot)
    snapshot["changes_since_previous"] = {
        "previous_snapshot": (prev or {}).get("snapshot_date"),
        "count": len(changes),
        "changes": changes,
    }

    write_json(os.path.join(SNAPSHOT_DIR, f"{date}.json"), snapshot)
    write_json(LATEST_JSON, snapshot)
    write_json(SPONSOR_ALIASES_JSON, aliases)

    queue = read_json(REVIEW_QUEUE_JSON, {"generated": None, "candidates": []}) or {}
    known_candidates = {c["nct_id"]: c for c in queue.get("candidates", [])}
    for cand in candidates:
        if cand["nct_id"] in known_candidates:
            known_candidates[cand["nct_id"]]["last_seen"] = date
        else:
            cand["last_seen"] = date
            known_candidates[cand["nct_id"]] = cand
    for nct, decision in decisions.items():
        known_candidates.pop(nct, None)
    write_json(
        REVIEW_QUEUE_JSON,
        {
            "generated": date,
            "note": (
                "Trials from tracked sponsors that are not linked to a drug in "
                "seed-list.csv. Nothing here is counted in the dashboard totals. "
                "Review each one against criteria.md, then either add a row to "
                "seed-list.csv or record a decision in data/review-decisions.json."
            ),
            "candidates": sorted(
                known_candidates.values(),
                key=lambda c: (c.get("start_date") or "", c["nct_id"]),
                reverse=True,
            ),
        },
    )

    log = read_json(CHANGELOG_JSON, []) or []
    log = [entry for entry in log if entry.get("date") != date]
    log.insert(
        0,
        {
            "date": date,
            "generated_at_utc": snapshot["generated_at_utc"],
            "previous_snapshot": (prev or {}).get("snapshot_date"),
            "mode": snapshot["mode"],
            "totals": snapshot["stats"],
            "change_count": len(changes),
            "changes": changes,
            "warnings": warnings,
        },
    )
    write_json(CHANGELOG_JSON, log)

    print(
        f"\n{date}: {snapshot['stats']['drugs']} drugs, "
        f"{snapshot['stats']['trials']} trials, {len(candidates)} candidates, "
        f"{len(changes)} changes, {client.calls} API calls, {len(warnings)} warnings",
        file=sys.stderr,
    )
    for warning in warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="only the first N drugs")
    parser.add_argument("--offline", action="store_true", help="reuse cached trial data")
    parser.add_argument("--skip-sponsors", action="store_true", help="skip the sponsor sweep")
    args = parser.parse_args()
    run(limit=args.limit, offline=args.offline, skip_sponsors=args.skip_sponsors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
