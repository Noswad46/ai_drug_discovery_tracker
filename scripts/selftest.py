"""Unit tests for the parts that are easy to get subtly wrong.

    python3 scripts/selftest.py

No network: every test runs against fixtures built in memory.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import refresh  # noqa: E402
from ctgov import flatten  # noqa: E402


def drug(**overrides) -> dict:
    base = {
        "key": "ism6331",
        "drug_name": "ISM6331",
        "alt_codes": [],
        "company": "Insilico Medicine",
        "company_clean": "Insilico Medicine",
        "partner": "",
        "partner_clean": "",
        "seed_phase": "Phase 1",
        "seed_status": "active: dosing",
        "status_class": "active",
        "first_in_human_date": "",
        "seed_nct_ids": [],
    }
    base.update(overrides)
    return base


def trial(**overrides) -> dict:
    base = {
        "nct_id": "NCT00000001",
        "title": "A Study of ISM6331 in Solid Tumours",
        "org_study_id": "ISM6331-101",
        "secondary_ids": [],
        "interventions": ["ISM6331 capsules"],
        "lead_sponsor": "InSilico Medicine Hong Kong Limited",
        "collaborators": [],
        "overall_status": "RECRUITING",
        "phases": ["PHASE1"],
        "enrollment": 40,
        "start_date": "2024-12-27",
        "primary_completion_date": "2027-01",
        "last_update_posted": "2026-11-01",
        "has_results": False,
    }
    base.update(overrides)
    return base


class PhaseScale(unittest.TestCase):
    def test_combined_phases_map_to_their_own_step(self):
        self.assertEqual(common.ctg_phase(["PHASE1", "PHASE2"]), "Phase 1/2")
        self.assertEqual(common.ctg_phase(["PHASE2", "PHASE3"]), "Phase 2/3")
        self.assertEqual(common.ctg_phase(["PHASE3"]), "Phase 3")
        self.assertEqual(common.ctg_phase(["PHASE4"]), "Approved")
        self.assertIsNone(common.ctg_phase(["NA"]))
        self.assertIsNone(common.ctg_phase([]))

    def test_combined_phases_floor_down_in_the_funnel(self):
        self.assertEqual(common.FUNNEL_BUCKET["Phase 1/2"], "Phase 1")
        self.assertEqual(common.FUNNEL_BUCKET["Phase 2/3"], "Phase 2")

    def test_rank_is_monotonic(self):
        ranks = [common.phase_rank(p) for p in common.PHASE_SCALE]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(common.phase_rank(None), -1)


class SeedParsing(unittest.TestCase):
    def test_status_class(self):
        self.assertEqual(common.status_class("active: dosing"), "active")
        self.assertEqual(common.status_class("discontinued May 2025"), "discontinued")
        self.assertEqual(common.status_class("deprioritised: seeking partner"), "deprioritised")
        self.assertEqual(common.status_class("approved: FDA EUA 2020"), "approved")
        self.assertEqual(common.status_class(""), "unclear")

    def test_company_cleaning(self):
        self.assertEqual(common.clean_company("Recursion (ex-Exscientia)"), "Recursion")
        self.assertEqual(common.clean_company("Takeda"), "Takeda")

    def test_therapy_area_grouping(self):
        self.assertEqual(common.therapy_area_group("other (women's health)"), "other")
        self.assertEqual(common.therapy_area_group("oncology"), "oncology")
        self.assertEqual(common.therapy_area_group("CNS"), "CNS")

    def test_tokens_split_codes_from_names(self):
        tokens = common.search_tokens(
            drug(drug_name="baricitinib (COVID-19)", alt_codes=["Olumiant"])
        )
        kinds = {token["text"]: token["kind"] for token in tokens}
        self.assertEqual(kinds, {"baricitinib": "word", "Olumiant": "word"})
        tokens = common.search_tokens(drug(drug_name="rentosertib", alt_codes=["INS018_055"]))
        kinds = {token["text"]: token["kind"] for token in tokens}
        self.assertEqual(kinds["INS018_055"], "code")

    def test_seed_file_parses(self):
        rows = common.load_seed()
        self.assertGreater(len(rows), 10)
        self.assertEqual(len({row["key"] for row in rows}), len(rows))
        for row in rows:
            self.assertIn(row["status_class"], common.STATUS_CLASSES)
            self.assertIn(row["therapy_area_group"], common.THERAPY_AREAS)
            if row["seed_phase"]:
                self.assertIn(row["seed_phase"], common.PHASE_SCALE)


class Matching(unittest.TestCase):
    def test_code_in_title_is_enough(self):
        target = drug()
        tokens = common.search_tokens(target)
        self.assertEqual(
            refresh.match_reason(target, tokens, trial(), {}), "code:ISM6331"
        )

    def test_code_matches_across_punctuation(self):
        target = drug(drug_name="rentosertib", alt_codes=["ISM001-055"])
        tokens = common.search_tokens(target)
        found = trial(title="Study of ISM001_055 in IPF", interventions=["ISM001 055 tablets"])
        self.assertTrue(refresh.match_reason(target, tokens, found, {}))

    def test_generic_name_needs_the_sponsor_too(self):
        target = drug(
            drug_name="baricitinib (COVID-19)",
            company="Eli Lilly",
            company_clean="Eli Lilly",
        )
        tokens = common.search_tokens(target)
        someone_else = trial(
            title="Baricitinib in Rheumatoid Arthritis",
            interventions=["Baricitinib"],
            org_study_id="X",
            lead_sponsor="A University Hospital",
        )
        self.assertIsNone(refresh.match_reason(target, tokens, someone_else, {}))
        lilly = dict(someone_else, lead_sponsor="Eli Lilly and Company")
        self.assertEqual(
            refresh.match_reason(target, tokens, lilly, {}), "name+sponsor:baricitinib"
        )

    def test_unrelated_trial_does_not_match(self):
        target = drug()
        tokens = common.search_tokens(target)
        other = trial(
            title="A Study of Pembrolizumab",
            org_study_id="KEYNOTE-001",
            interventions=["Pembrolizumab"],
        )
        self.assertIsNone(refresh.match_reason(target, tokens, other, {}))

    def test_sponsor_match_sees_through_subsidiaries(self):
        target = drug(company_clean="Structure Therapeutics")
        found = trial(
            lead_sponsor="Gasherbrum Bio, Inc., a wholly owned subsidiary of Structure Therapeutics"
        )
        self.assertTrue(refresh.sponsor_matches(target, found, {}))


class Derivation(unittest.TestCase):
    def test_ctgov_can_raise_the_phase_but_the_flag_is_set(self):
        derived = refresh.derive(drug(), [trial(phases=["PHASE1", "PHASE2"])])
        self.assertEqual(derived["phase"], "Phase 1/2")
        self.assertEqual(derived["phase_basis"], "ctgov")
        self.assertTrue(derived["phase_ahead_of_seed"])
        self.assertEqual(derived["phase_bucket"], "Phase 1")

    def test_seed_wins_where_ctgov_has_no_concept(self):
        derived = refresh.derive(
            drug(seed_phase="Filed", status_class="active"), [trial(phases=["PHASE3"])]
        )
        self.assertEqual(derived["phase"], "Filed")
        self.assertEqual(derived["phase_basis"], "both")
        self.assertFalse(derived["phase_ahead_of_seed"])

    def test_unclear_status_resolves_from_a_recruiting_trial(self):
        derived = refresh.derive(drug(status_class="unclear"), [trial()])
        self.assertEqual(derived["status"], "active")
        self.assertTrue(derived["status_resolved_from_ctgov"])

    def test_discontinued_stays_discontinued(self):
        derived = refresh.derive(
            drug(status_class="discontinued"), [trial(overall_status="COMPLETED")]
        )
        self.assertEqual(derived["status"], "discontinued")

    def test_enrolment_and_stage_dates(self):
        derived = refresh.derive(
            drug(),
            [
                trial(nct_id="NCT1", enrollment=40, phases=["PHASE1"], start_date="2023-01-05"),
                trial(nct_id="NCT2", enrollment=60, phases=["PHASE2"], start_date="2025-06-01"),
                trial(nct_id="NCT3", enrollment=10, phases=["PHASE2"], start_date="2024-02-01"),
            ],
        )
        self.assertEqual(derived["total_enrolment"], 110)
        self.assertEqual(derived["first_trial_start"], "2023-01-05")
        self.assertEqual(derived["stage_first_start"]["Phase 2"], "2024-02-01")

    def test_no_trials_leaves_the_seed_values_alone(self):
        derived = refresh.derive(drug(seed_phase="Phase 2", status_class="unclear"), [])
        self.assertEqual(derived["phase"], "Phase 2")
        self.assertEqual(derived["status"], "unclear")
        self.assertIsNone(derived["total_enrolment"])


class Diffing(unittest.TestCase):
    def snapshot(self, phase, status, trials, candidates=()):
        record = drug()
        record["derived"] = {"phase": phase, "status": status}
        record["trials"] = list(trials)
        return {
            "snapshot_date": "2026-11-11",
            "drugs": [record],
            "candidates": [{"nct_id": nct, "title": "t"} for nct in candidates],
        }

    def test_detects_a_phase_move(self):
        before = self.snapshot("Phase 1", "active", [trial()])
        after = self.snapshot("Phase 2", "active", [trial()])
        changes = refresh.diff_snapshots(before, after)
        self.assertEqual([c["type"] for c in changes], ["phase_change"])
        self.assertEqual(changes[0]["direction"], "up")

    def test_detects_trial_level_news(self):
        before = self.snapshot("Phase 1", "active", [trial()])
        after = self.snapshot(
            "Phase 1",
            "active",
            [
                trial(overall_status="COMPLETED", has_results=True, enrollment=55),
                trial(nct_id="NCT00000002"),
            ],
        )
        kinds = sorted(c["type"] for c in refresh.diff_snapshots(before, after))
        self.assertEqual(
            kinds,
            ["enrolment_change", "results_posted", "trial_added", "trial_status_change"],
        )

    def test_new_candidate_is_flagged_not_counted(self):
        before = self.snapshot("Phase 1", "active", [trial()])
        after = self.snapshot("Phase 1", "active", [trial()], candidates=["NCT09999999"])
        changes = refresh.diff_snapshots(before, after)
        self.assertEqual([c["type"] for c in changes], ["candidate_flagged"])

    def test_nothing_changed_means_no_entries(self):
        before = self.snapshot("Phase 1", "active", [trial()])
        after = self.snapshot("Phase 1", "active", [trial()])
        self.assertEqual(refresh.diff_snapshots(before, after), [])

    def test_first_snapshot_has_nothing_to_diff(self):
        after = self.snapshot("Phase 1", "active", [trial()])
        self.assertEqual(refresh.diff_snapshots(None, after), [])


class CatalystDates(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(
            common.parse_catalyst_date("2027-Q1"),
            {"start": "2027-01-01", "end": "2027-03-31", "label": "Q1 2027", "granularity": "quarter"},
        )
        self.assertEqual(common.parse_catalyst_date("2026-H2")["end"], "2026-12-31")
        self.assertEqual(common.parse_catalyst_date("2027")["start"], "2027-01-01")
        self.assertEqual(common.parse_catalyst_date("2027-02")["end"], "2027-02-28")
        self.assertEqual(common.parse_catalyst_date("2028-02")["end"], "2028-02-29")  # leap
        self.assertEqual(common.parse_catalyst_date("2027-03-15")["label"], "15 Mar 2027")

    def test_rejects_what_it_cannot_read(self):
        for value in ("next spring", "2027-Q5", "2027-13", "2027-02-30", "", "Q1 2027"):
            self.assertIsNone(common.parse_catalyst_date(value), value)

    def test_months_between(self):
        self.assertEqual(common.months_between("2026-11-19", "2027-01-01"), 2)
        self.assertEqual(common.months_between("2027-01-01", "2026-11-19"), -2)

    def test_catalyst_file_is_valid(self):
        """Every hand-written entry has to resolve, or the strip lies."""
        raw = common.read_json(common.CATALYSTS_JSON, {}) or {}
        keys = {row["key"] for row in common.load_seed()}
        self.assertTrue(common.parse_catalyst_date(raw.get("last_reviewed", "")))
        self.assertTrue(raw.get("catalysts"))
        for entry in raw["catalysts"]:
            self.assertIsNotNone(
                common.parse_catalyst_date(entry.get("date", "")),
                f"unreadable date: {entry.get('date')!r}",
            )
            self.assertIn(entry.get("type"), common.CATALYST_TYPES, entry.get("title"))
            self.assertTrue(entry.get("title"))
            if entry.get("drug_key"):
                self.assertIn(entry["drug_key"], keys, "unknown drug_key")
            else:
                self.assertTrue(entry.get("subject"), "an entry needs a drug_key or a subject")


class Flattening(unittest.TestCase):
    def test_flatten_reads_the_api_shape(self):
        record = {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT06253871",
                    "briefTitle": "A Phase 1/2 Study of IAM1363",
                    "orgStudyIdInfo": {"id": "IAM1363-01"},
                    "secondaryIdInfos": [{"id": "2024-519188-17-00"}],
                },
                "statusModule": {
                    "overallStatus": "RECRUITING",
                    "startDateStruct": {"date": "2024-03-25"},
                    "lastUpdatePostDateStruct": {"date": "2026-11-17"},
                },
                "designModule": {
                    "studyType": "INTERVENTIONAL",
                    "phases": ["PHASE1", "PHASE2"],
                    "enrollmentInfo": {"count": 150, "type": "ESTIMATED"},
                },
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Iambic Therapeutics"}},
                "armsInterventionsModule": {
                    "interventions": [{"name": "IAM1363", "otherNames": ["IAM-1363"]}]
                },
                "contactsLocationsModule": {
                    "locations": [{"country": "United States"}, {"country": "United States"}]
                },
            },
            "hasResults": False,
        }
        flat = flatten(record)
        self.assertEqual(flat["nct_id"], "NCT06253871")
        self.assertEqual(flat["phases"], ["PHASE1", "PHASE2"])
        self.assertEqual(flat["enrollment"], 150)
        self.assertEqual(flat["interventions"], ["IAM1363", "IAM-1363"])
        self.assertEqual(flat["countries"], ["United States"])
        self.assertEqual(flat["url"], "https://clinicaltrials.gov/study/NCT06253871")


class PublishedPayloadIsClean(unittest.TestCase):
    """The deployed site is site/ as committed, so these files are the public surface."""

    SITE_DATA = os.path.join(common.ROOT, "site", "data")

    def test_review_candidates_never_reach_the_published_payload(self):
        """Candidates are unvetted; publishing them would make claims we have not checked."""
        queue = common.read_json(common.REVIEW_QUEUE_JSON, {}) or {}
        ncts = [c["nct_id"] for c in queue.get("candidates", []) if c.get("nct_id")]
        for name in ("dashboard.js", "dashboard.json"):
            path = os.path.join(self.SITE_DATA, name)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as fh:
                blob = fh.read()
            self.assertNotIn("review_queue", blob, f"{name} carries the review queue")
            leaked = [nct for nct in ncts if nct in blob]
            self.assertEqual(leaked, [], f"{name} names review candidates: {leaked}")

    def test_the_local_review_file_is_not_committed(self):
        """build.py --include-review writes it; .gitignore has to keep it out of the repo."""
        with open(os.path.join(common.ROOT, ".gitignore"), encoding="utf-8") as fh:
            self.assertIn("site/data/review.js", fh.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
