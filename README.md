# AI-designed drugs in clinical development — tracker

A static dashboard that tracks every drug on `seed-list.csv` whose discovery used AI or
machine learning materially, against live ClinicalTrials.gov records. Each refresh is
stored as a dated snapshot, so phase moves, status changes and attrition are visible over
time rather than only as a current-state list.

No dependencies, no build toolchain, no server: Python standard library for the refresh,
one HTML page plus one JS file for the dashboard, free hosting on GitHub Pages.

## Quick start

```bash
python3 scripts/refresh.py     # pull live trial data, write a dated snapshot (~2 min, ~140 API calls)
python3 scripts/build.py       # rebuild the dashboard payload (publish-safe)
open site/index.html           # works straight from file://

python3 scripts/build.py --include-review   # same, plus the review queue, for your eyes only
```

`refresh.py` is safe to re-run on the same day — it overwrites that day's snapshot and its
changelog entry rather than appending a duplicate.

Useful flags:

| Command | What it does |
|---|---|
| `python3 scripts/refresh.py --limit 5` | first five drugs only — a quick smoke test |
| `python3 scripts/refresh.py --skip-sponsors` | skip the sponsor sweep (faster, finds no new candidates) |
| `python3 scripts/refresh.py --offline` | re-derive the snapshot from cached trial data, no network |
| `python3 scripts/report.py` | markdown summary of the last refresh |
| `python3 scripts/report.py --new-only` | just the candidates first seen at the last refresh |
| `python3 scripts/selftest.py` | 25 unit tests over phase mapping, matching and diffing (no network) |

## What the refresh does

For each row of `seed-list.csv`:

1. **Searches** ClinicalTrials.gov v2 (`query.intr`) for the drug's codes and INN.
2. **Matches** a trial to the drug only when a drug code appears in its title, intervention,
   protocol ID or secondary IDs. For a name that is also a conventionally discovered
   medicine (`baricitinib`, `tempol`), the sponsor has to match as well.
3. **Adds** any NCT IDs already curated in the seed list, and anything pinned in
   `data/overrides.json`.
4. **Derives** highest phase, status, enrolment, results-posted and the first trial start at
   or above each phase. Phase is the higher of the curated value and what the API shows;
   where the API is ahead, the row is flagged on the page for a human to confirm rather than
   silently rewriting the seed list.
5. **Sweeps** each tracked sponsor for interventional trials. Anything matching a tracked
   drug is attached; anything else recent becomes a **review candidate** — never a new row.

Then it diffs the result against the previous snapshot to produce the changelog, writes
`data/snapshots/<date>.json`, and updates `data/latest.json`, `data/changelog.json` and
`data/review-queue.json`.

Resilience: if the API cannot be reached for a drug, the previous snapshot's trials are kept
and the row is marked as cached on the page, so one bad call never empties the dataset.

## Nothing is added automatically

Discovery only ever writes to `data/review-queue.json`. Clearing the queue is a manual job,
and it is yours:

* it qualifies → add a row to `seed-list.csv` (and record `"decision": "accepted"` in
  `data/review-decisions.json` so it stops being re-flagged), or
* it does not → record `"decision": "rejected"` with a note in
  `data/review-decisions.json`.

Three places show you the queue, none of them the published site:

* the **review issue** the weekly Action opens, listing only newly-seen candidates,
* `python3 scripts/report.py --new-only`, which prints the same list locally, and
* the dashboard's review section, which appears only after
  `python3 scripts/build.py --include-review`.

That last build writes `site/data/review.js`, which is gitignored; a plain build deletes it
again. Candidates are unvetted — publishing them would put unchecked AI-design claims about
named companies on a public page — so they are kept out of `site/data/dashboard.{js,json}`
entirely. Both workflows deploy `site/` as committed, so keeping the file out of git is what
keeps the queue off the web.

Sponsors with more than 80 interventional trials (Lilly, Takeda, BMS, Sumitomo) are not
swept — their AI assets are tracked by drug code instead, which keeps the queue reviewable.
Both thresholds live at the top of `scripts/refresh.py`.

## Keeping the catalysts current

`data/catalysts.json` is the one file meant to be edited often, and the dashboard never
writes to it. An entry looks like this:

```json
{
  "drug_key": "zasocitinib",
  "date": "2027-Q1",
  "type": "fda_decision",
  "title": "PDUFA date",
  "detail": "FDA decision on the NDA for moderate-to-severe plaque psoriasis…",
  "source_note": "Takeda news release",
  "source_url": "https://www.takeda.com/newsroom/…"
}
```

* `date` takes `YYYY`, `YYYY-MM`, `YYYY-MM-DD`, `YYYY-Qn` or `YYYY-Hn`. Use the coarsest form
  the company actually gave — "early 2027" is `2027-H1`, not a made-up day — so the page
  never implies more precision than the source.
* `drug_key` is the slug of `drug_name` in `seed-list.csv`, validated at build time and by
  `scripts/selftest.py`. Omit it and give `subject` + `company` instead for watchlist
  programmes that have not reached the clinic.
* `type` is one of `fda_decision`, `readout`, `trial_start`, `regulatory`, `presentation`,
  `other`.
* Entries past their window are shown last, flagged **past guidance**, and dropped after a
  year. That is deliberate: a readout that never arrived is itself informative.
* The weekly job summary lists anything overdue or due within two months, so the file gets a
  nudge rather than quietly going stale. `last_reviewed` is printed on the page.

Underneath the strip, the page also lists the next primary completion dates for active drugs
straight from ClinicalTrials.gov. Those need no maintenance, but they are estimates that slip
often — which is why they are kept visually separate from the curated entries.

## Scheduling

`.github/workflows/refresh.yml` runs every Monday at 06:17 UTC, commits the new snapshot and
rebuilt payload, writes a job summary, opens a **review issue** when the sweep found new
candidates, and redeploys the page. For a monthly cadence, change the cron to `17 6 1 * *`.
It can also be run on demand from the Actions tab.

`.github/workflows/publish.yml` redeploys the page when `site/` changes on `main`.

Publishing is opt-in, so the refresh stays green whether or not the site is hosted:

1. Push the repository to GitHub. The refresh already works at this point — it commits a
   snapshot every week and you read the page locally.
2. To host the page: Settings → Pages → Source: **GitHub Actions**. GitHub Pages serves from
   a private repository only on a paid plan; on GitHub Free the repository has to be public.
   Either way the published site is readable by anyone with the URL.
3. Then set the repository variable `PUBLISH_PAGES` to `true`
   (`gh variable set PUBLISH_PAGES --body true`). The deploy steps are skipped until it is.

The workflow needs no secrets — ClinicalTrials.gov's API is public and unauthenticated.

## Layout

```
seed-list.csv              the curated input — one row per drug (edit this by hand)
criteria.md                inclusion criteria; rendered into the page at build time
scripts/
  common.py                seed parsing, phase scale, shared helpers
  ctgov.py                 ClinicalTrials.gov v2 client (stdlib only, retries, rate limit)
  refresh.py               fetch -> derive -> snapshot -> diff -> changelog -> review queue
  build.py                 snapshots -> site/data/dashboard.{js,json}; --include-review
                           also writes the gitignored site/data/review.js
  report.py                markdown summary for the Action
  selftest.py              unit tests (no network)
data/
  snapshots/<date>.json    one file per refresh — the history the timeline is built from
  latest.json              the most recent snapshot
  changelog.json           what changed at each refresh
  review-queue.json        candidates awaiting manual review
  review-decisions.json    your verdicts on them (hand-edited)
  overrides.json           pinned/excluded trials and discovery opt-outs (hand-edited)
  benchmarks.json          published success rates used for comparison (hand-edited)
  catalysts.json           upcoming readouts and decision dates (hand-edited)
  sponsor-aliases.json     sponsor spellings learned from matched trials (auto)
site/
  index.html               static shell
  assets/styles.css        palette, mark specs, light and dark themes
  assets/app.js            all rendering: tiles, funnel, timeline, breakdowns, table
  data/dashboard.js        generated payload (also as .json)
.github/workflows/         scheduled refresh and Pages publish
```

## On the page

* **Headline tiles** — drugs tracked, active development, Phase 3 or beyond, trials linked,
  and changes at this refresh.
* **Upcoming catalysts** — a strip of dated events worth returning for: regulatory decisions
  (zasocitinib's PDUFA in Q1 2027), guided readouts and planned trial starts, including
  watchlist programmes that are not clinical-stage yet. Hand-maintained; see below.
* **Pipeline funnel** — how far the cohort has got, with stage-to-stage progression compared
  against published rates (industry 2011–2020 and the BCG/Wellcome AI analysis, about 40% at
  Phase 2). Cohort rates use only drugs with a *resolved* outcome at that stage and always
  show their denominator, because most of this cohort is too young to have progressed or
  failed yet.
* **Phase timeline** — cumulative count of drugs that had reached each phase by year, built
  from trial start dates so it reaches back before the tracker existed, plus the phase
  changes recorded between refreshes.
* **Breakdowns** — how AI was used, therapy area, modality and company, each split by phase.
* **Drug table** — searchable and sortable, with the **AI role** (target ID / molecule design
  / both / repurposing / personalised design) and the **platform** that made the claim as
  first-class columns, since that is what puts a row on this list at all. Hover either for
  the seed list's full wording. A row opens its trials with links to ClinicalTrials.gov and
  to the source the row was checked against. Exports the filtered rows as CSV.
* **Changelog** — every refresh and what it changed.
* **Review queue** — possible new entries, explicitly excluded from every count above.
* **Inclusion criteria and method** — rendered from `criteria.md`, with the refresh date,
  seed-list checksum and any warnings from the last run.

Filters — AI role, platform, therapy area, modality, company, phase, status, confidence,
plus a strict view that drops repurposing and personalised design — apply to every chart and
to the table at once, and are kept in the URL hash, so a filtered view can be shared as a
link (`#role=repurposing`, `#platform=Recursion OS`). Platform spellings are collapsed to
their family, so `Pharma.AI` and `Pharma.AI (Chemistry42)` are one filter entry.

## Caveats worth keeping in mind

* The funnel is a snapshot of a young cohort. Its stage-to-stage ratios are **not** success
  rates; the benchmark panel quotes the resolved-outcome rate with its denominator instead.
* Chinese and Australian registry entries (CTR/ACTRN) are not covered by the
  ClinicalTrials.gov API. A drug with no linked trial is marked as such — it means "not
  found on ClinicalTrials.gov", not "no trial".
* `confidence` and `ai_role` are editorial judgements from `criteria.md`, not facts from the
  registry. The strict view filters out AI repurposing and algorithmic personalised design.
* Benchmark values in `data/benchmarks.json` are hand-entered with citations. Check them
  before quoting them.
