"""Shared helpers for the AI-designed drug tracker.

Pure standard library on purpose: the refresh runs in a bare GitHub Actions
runner with no `pip install` step, so there is nothing to pin or break.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")
SITE_DIR = os.path.join(ROOT, "site")
SITE_DATA_DIR = os.path.join(SITE_DIR, "data")
SEED_CSV = os.path.join(ROOT, "seed-list.csv")
CRITERIA_MD = os.path.join(ROOT, "criteria.md")

LATEST_JSON = os.path.join(DATA_DIR, "latest.json")
CHANGELOG_JSON = os.path.join(DATA_DIR, "changelog.json")
REVIEW_QUEUE_JSON = os.path.join(DATA_DIR, "review-queue.json")
REVIEW_DECISIONS_JSON = os.path.join(DATA_DIR, "review-decisions.json")
OVERRIDES_JSON = os.path.join(DATA_DIR, "overrides.json")
BENCHMARKS_JSON = os.path.join(DATA_DIR, "benchmarks.json")
SPONSOR_ALIASES_JSON = os.path.join(DATA_DIR, "sponsor-aliases.json")

# --------------------------------------------------------------------------
# phases
# --------------------------------------------------------------------------

# One ordered scale for both the curated seed column and ClinicalTrials.gov,
# so "highest phase reached" is comparable across the two sources.
PHASE_SCALE = [
    "Preclinical",
    "Phase 1",
    "Phase 1/2",
    "Phase 2",
    "Phase 2/3",
    "Phase 3",
    "Filed",
    "Approved",
]
PHASE_RANK = {name: i for i, name in enumerate(PHASE_SCALE)}

# ClinicalTrials.gov `designModule.phases` -> our scale.
CTG_PHASE_MAP = {
    ("EARLY_PHASE1",): "Phase 1",
    ("PHASE1",): "Phase 1",
    ("PHASE1", "PHASE2"): "Phase 1/2",
    ("PHASE2",): "Phase 2",
    ("PHASE2", "PHASE3"): "Phase 2/3",
    ("PHASE3",): "Phase 3",
    # A Phase 4 trial only exists post-approval.
    ("PHASE4",): "Approved",
}

# Buckets used by the funnel: a Phase 1/2 drug has not cleared Phase 1, and a
# Phase 2/3 drug has not cleared Phase 2, so each combined phase floors down.
FUNNEL_STAGES = ["Phase 1", "Phase 2", "Phase 3", "Filed", "Approved"]
FUNNEL_BUCKET = {
    "Phase 1": "Phase 1",
    "Phase 1/2": "Phase 1",
    "Phase 2": "Phase 2",
    "Phase 2/3": "Phase 2",
    "Phase 3": "Phase 3",
    "Filed": "Filed",
    "Approved": "Approved",
}

CTG_STATUS_CLASS = {
    "NOT_YET_RECRUITING": "active",
    "RECRUITING": "active",
    "ENROLLING_BY_INVITATION": "active",
    "ACTIVE_NOT_RECRUITING": "active",
    "AVAILABLE": "active",
    "COMPLETED": "completed",
    "TERMINATED": "stopped",
    "WITHDRAWN": "stopped",
    "SUSPENDED": "stopped",
    "UNKNOWN": "unknown",
    "NO_LONGER_AVAILABLE": "other",
    "TEMPORARILY_NOT_AVAILABLE": "other",
    "APPROVED_FOR_MARKETING": "other",
    "WITHHELD": "other",
}

STATUS_CLASSES = [
    "active",
    "approved",
    "completed",
    "deprioritised",
    "discontinued",
    "unclear",
]


def phase_rank(phase: str | None) -> int:
    """Rank on PHASE_SCALE; unknown / missing phases sort below Phase 1."""
    if not phase:
        return -1
    return PHASE_RANK.get(phase, -1)


def ctg_phase(phases: list[str] | None) -> str | None:
    if not phases:
        return None
    key = tuple(sorted({p for p in phases if p and p != "NA"}))
    if not key:
        return None
    return CTG_PHASE_MAP.get(key)


def status_class(raw_status: str) -> str:
    """First word of the curated `status` column, normalised."""
    if not raw_status:
        return "unclear"
    head = re.split(r"[\s:;,(]", raw_status.strip(), maxsplit=1)[0]
    head = head.strip(" :;,.").lower()
    aliases = {
        "approved": "approved",
        "active": "active",
        "completed": "completed",
        "discontinued": "discontinued",
        "terminated": "discontinued",
        "deprioritised": "deprioritised",
        "deprioritized": "deprioritised",
        "paused": "deprioritised",
        "unclear": "unclear",
    }
    return aliases.get(head, "unclear")


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------

NCT_RE = re.compile(r"\bNCT\d{8}\b", re.I)


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "unknown"


def squash(value: str) -> str:
    """Lowercase, strip every non-alphanumeric character.

    `INS018_055`, `ins018-055` and `INS018 055` all collapse to `ins018055`,
    which is how trial records spell the same code three different ways.
    """
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def split_multi(value: str) -> list[str]:
    """Split a `;`-separated seed-list cell."""
    if not value:
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def clean_company(value: str) -> str:
    """`Recursion (ex-Exscientia)` -> `Recursion`."""
    value = re.sub(r"\(.*?\)", " ", value or "")
    value = re.split(r"\b(?:and|licensed|acquired)\b", value, maxsplit=1, flags=re.I)[0]
    return re.sub(r"\s+", " ", value).strip(" ,;/")


def today_iso() -> str:
    override = os.environ.get("TRACKER_DATE")
    if override:
        return override
    return _dt.date.today().isoformat()


def now_utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------


def read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return default


def write_json(path: str, payload, indent: int = 2) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=indent, ensure_ascii=False, sort_keys=False)
        fh.write("\n")


def load_seed(path: str = SEED_CSV) -> list[dict]:
    """Read seed-list.csv into normalised drug records."""
    rows: list[dict] = []
    seen_keys: set[str] = set()
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            name = (raw.get("drug_name") or "").strip()
            if not name:
                continue
            key = slugify(name)
            if key in seen_keys:  # keep keys unique even if two rows share a name
                key = f"{key}-{slugify(raw.get('company', ''))}"
            seen_keys.add(key)

            nct_cell = raw.get("nct_ids") or ""
            nct_ids = sorted({m.upper() for m in NCT_RE.findall(nct_cell)})
            other_registry = [
                part for part in split_multi(nct_cell) if not NCT_RE.search(part)
            ]

            raw_status = (raw.get("status") or "").strip()
            rows.append(
                {
                    "key": key,
                    "drug_name": name,
                    "alt_codes": split_multi(raw.get("alt_codes", "")),
                    "company": (raw.get("company") or "").strip(),
                    "company_clean": clean_company(raw.get("company", "")),
                    "partner": (raw.get("partner") or "").strip(),
                    "partner_clean": clean_company(raw.get("partner", "")),
                    "ai_platform": (raw.get("ai_platform") or "").strip(),
                    "modality": (raw.get("modality") or "").strip(),
                    "modality_group": modality_group(raw.get("modality", "")),
                    "target": (raw.get("target") or "").strip(),
                    "indication": (raw.get("indication") or "").strip(),
                    "therapy_area": (raw.get("therapy_area") or "").strip(),
                    "therapy_area_group": therapy_area_group(raw.get("therapy_area", "")),
                    "seed_phase": (raw.get("highest_phase") or "").strip(),
                    "seed_status": raw_status,
                    "status_class": status_class(raw_status),
                    "first_in_human_date": (raw.get("first_in_human_date") or "").strip(),
                    "seed_nct_ids": nct_ids,
                    "other_registry_ids": other_registry,
                    "ai_role": (raw.get("ai_role") or "").strip(),
                    "ai_role_group": ai_role_group(raw.get("ai_role", "")),
                    "confidence": (raw.get("confidence") or "").strip().lower(),
                    "source_url": (raw.get("source_url") or "").strip(),
                    "last_checked": (raw.get("last_checked") or "").strip(),
                    "needs_verification": "VERIFY" in raw_status.upper(),
                }
            )
    return rows


THERAPY_AREAS = [
    "oncology",
    "immunology/inflammation",
    "fibrosis",
    "metabolic",
    "CNS",
    "infectious",
    "rare/genetic",
    "ophthalmology",
    "cardiovascular",
    "other",
]


def therapy_area_group(value: str) -> str:
    """Canonical therapy area: `other (women's health)` -> `other`."""
    cleaned = re.sub(r"\(.*?\)", " ", value or "").strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    for area in THERAPY_AREAS:
        if cleaned == area.lower():
            return area
    return "other"


def modality_group(value: str) -> str:
    """Collapse the free-text modality column into chartable groups."""
    v = (value or "").lower()
    if not v:
        return "unknown"
    if "antibody" in v:
        return "antibody"
    if "vaccine" in v:
        return "vaccine"
    if "cell therapy" in v or "car-t" in v:
        return "cell therapy"
    if "peptide" in v:
        return "peptide"
    if "mrna" in v or "rna" in v and "targeting" not in v:
        return "nucleic acid"
    if "small molecule" in v:
        return "small molecule"
    return "other"


def ai_role_group(value: str) -> str:
    v = (value or "").lower()
    if v.startswith("both") or ("target" in v and "design" in v):
        return "both"
    if "repurpos" in v or "rescue" in v:
        return "repurposing"
    if "personalised" in v or "personalized" in v:
        return "personalised_design"
    if "target_id" in v or "target id" in v:
        return "target_id"
    if "design" in v:
        return "molecule_design"
    return "other"


def search_tokens(drug: dict) -> list[dict]:
    """Query tokens for a drug, tagged `code` or `word`.

    `code` tokens (they contain a digit) are specific enough to accept a trial
    on their own. `word` tokens — INNs such as `baricitinib` or `tempol`, which
    also belong to conventionally discovered medicines — only count when the
    sponsor matches too.
    """
    tokens: list[dict] = []
    seen: set[str] = set()
    for raw in [drug["drug_name"]] + drug["alt_codes"]:
        # `baricitinib (COVID-19)` -> `baricitinib`
        cleaned = re.sub(r"\(.*?\)", " ", raw).strip()
        for part in re.split(r"\s*/\s*", cleaned):
            part = part.strip()
            squashed = squash(part)
            if len(squashed) < 5 or squashed in seen:
                continue
            seen.add(squashed)
            kind = "code" if any(ch.isdigit() for ch in squashed) else "word"
            tokens.append({"text": part, "squashed": squashed, "kind": kind})
    return tokens
