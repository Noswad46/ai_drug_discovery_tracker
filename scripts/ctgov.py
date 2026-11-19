"""Minimal ClinicalTrials.gov API v2 client (standard library only).

Docs: https://clinicaltrials.gov/data-api/api
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://clinicaltrials.gov/api/v2"
USER_AGENT = "ai-drug-tracker/1.0 (static dashboard refresh; +https://clinicaltrials.gov/data-api)"

# Everything the dashboard reads off a trial. Asking for fields rather than the
# whole record keeps a full refresh in the low single-digit megabytes.
STUDY_FIELDS = [
    "NCTId",
    "BriefTitle",
    "OverallStatus",
    "LastKnownStatus",
    "WhyStopped",
    "Phase",
    "StudyType",
    "EnrollmentCount",
    "EnrollmentType",
    "StartDate",
    "PrimaryCompletionDate",
    "CompletionDate",
    "LeadSponsorName",
    "CollaboratorName",
    "Condition",
    "InterventionName",
    "InterventionOtherName",
    "OrgStudyId",
    "SecondaryId",
    "LastUpdatePostDate",
    "ResultsFirstPostDate",
    "HasResults",
    "LocationCountry",
]

ACTIVE_OR_DONE = (
    "NOT_YET_RECRUITING|RECRUITING|ENROLLING_BY_INVITATION|ACTIVE_NOT_RECRUITING"
    "|COMPLETED|TERMINATED|SUSPENDED|WITHDRAWN|UNKNOWN"
)


class CtgError(RuntimeError):
    pass


CA_BUNDLE_FALLBACKS = (
    os.environ.get("SSL_CERT_FILE"),
    os.environ.get("NIX_SSL_CERT_FILE"),
    "/etc/ssl/cert.pem",  # macOS / BSD system bundle
    "/etc/ssl/certs/ca-certificates.crt",  # Debian / Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL / Fedora
)


def _fallback_ssl_context() -> ssl.SSLContext | None:
    """A context built on the OS trust store.

    Verification still happens — this only covers hosts whose Python ships a
    CA bundle that misses a root the OS has (a corporate TLS-inspecting proxy,
    or a python.org build on macOS). Returns None if no bundle is on disk.
    """
    for path in CA_BUNDLE_FALLBACKS:
        if path and os.path.exists(path):
            try:
                return ssl.create_default_context(cafile=path)
            except (ssl.SSLError, OSError):
                continue
    return None


class Client:
    def __init__(self, delay: float = 0.34, retries: int = 4, timeout: int = 45):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.calls = 0
        self.errors = 0
        self._last_call = 0.0
        self._ssl = ssl.create_default_context()
        self._ssl_fallback_tried = False
        self.last_total: int | None = None

    # -- plumbing ---------------------------------------------------------
    def _get(self, path: str, params: dict) -> dict:
        query = urllib.parse.urlencode(params, doseq=True, quote_via=urllib.parse.quote)
        url = f"{BASE}{path}?{query}"
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            gap = time.monotonic() - self._last_call
            if gap < self.delay:
                time.sleep(self.delay - gap)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                self.calls += 1
                self._last_call = time.monotonic()
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self._ssl
                ) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code in (400, 404):  # a bad query will not get better
                    raise CtgError(f"{exc.code} for {url}") from exc
                time.sleep(min(2 ** attempt, 15))
            except urllib.error.URLError as exc:
                last_exc = exc
                if isinstance(exc.reason, ssl.SSLCertVerificationError):
                    if self._ssl_fallback_tried:
                        raise CtgError(f"TLS verification failed for {url}: {exc}") from exc
                    self._ssl_fallback_tried = True
                    fallback = _fallback_ssl_context()
                    if fallback is None:
                        raise CtgError(f"TLS verification failed for {url}: {exc}") from exc
                    self._ssl = fallback
                    continue
                time.sleep(min(2 ** attempt, 15))
            except (TimeoutError, json.JSONDecodeError) as exc:
                last_exc = exc
                time.sleep(min(2 ** attempt, 15))
        self.errors += 1
        raise CtgError(f"giving up on {url}: {last_exc}")

    # -- endpoints --------------------------------------------------------
    def studies_by_id(self, nct_ids: list[str]) -> list[dict]:
        """Fetch specific trials. Batched, because `filter.ids` takes a list."""
        out: list[dict] = []
        batch_size = 20
        for start in range(0, len(nct_ids), batch_size):
            batch = nct_ids[start : start + batch_size]
            payload = self._get(
                "/studies",
                {
                    "filter.ids": "|".join(batch),
                    "fields": ",".join(STUDY_FIELDS),
                    "pageSize": len(batch),
                },
            )
            out.extend(payload.get("studies", []))
        return out

    def search(self, params: dict, max_pages: int = 3, page_size: int = 100) -> list[dict]:
        params = dict(params)
        params.setdefault("fields", ",".join(STUDY_FIELDS))
        params["pageSize"] = page_size
        params["countTotal"] = "true"
        studies: list[dict] = []
        token = None
        self.last_total = None
        for _ in range(max_pages):
            if token:
                params["pageToken"] = token
            payload = self._get("/studies", params)
            if self.last_total is None:
                self.last_total = payload.get("totalCount")
            studies.extend(payload.get("studies", []))
            token = payload.get("nextPageToken")
            if not token:
                break
        return studies

    def search_intervention(self, expression: str) -> list[dict]:
        return self.search({"query.intr": expression}, max_pages=2, page_size=50)

    def _sponsor_params(self, sponsor: str) -> dict:
        return {
            "query.spons": sponsor,
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
            "filter.overallStatus": ACTIVE_OR_DONE,
        }

    def count_sponsor(self, sponsor: str) -> int:
        """How many interventional trials this sponsor search would return."""
        params = dict(self._sponsor_params(sponsor))
        params.update({"pageSize": 1, "countTotal": "true", "fields": "NCTId"})
        return int(self._get("/studies", params).get("totalCount") or 0)

    def search_sponsor(self, sponsor: str) -> list[dict]:
        return self.search(self._sponsor_params(sponsor), max_pages=3, page_size=100)


# -- record flattening ----------------------------------------------------


def _date(struct: dict | None) -> str | None:
    if not struct:
        return None
    return struct.get("date")


def flatten(study: dict) -> dict:
    """Turn an API record into the flat trial shape the dashboard stores."""
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    sponsors = protocol.get("sponsorCollaboratorsModule", {})
    arms = protocol.get("armsInterventionsModule", {})
    conditions = protocol.get("conditionsModule", {})
    locations = protocol.get("contactsLocationsModule", {}).get("locations", []) or []

    interventions = arms.get("interventions", []) or []
    intervention_names: list[str] = []
    for item in interventions:
        if item.get("name"):
            intervention_names.append(item["name"])
        intervention_names.extend(item.get("otherNames", []) or [])

    secondary_ids = [
        item.get("id", "") for item in ident.get("secondaryIdInfos", []) or []
    ]
    enrollment = design.get("enrollmentInfo", {}) or {}

    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "org_study_id": (ident.get("orgStudyIdInfo") or {}).get("id"),
        "secondary_ids": [sid for sid in secondary_ids if sid],
        "overall_status": status.get("overallStatus"),
        "last_known_status": status.get("lastKnownStatus"),
        "why_stopped": status.get("whyStopped"),
        "study_type": design.get("studyType"),
        "phases": design.get("phases", []) or [],
        "enrollment": enrollment.get("count"),
        "enrollment_type": enrollment.get("type"),
        "start_date": _date(status.get("startDateStruct")),
        "primary_completion_date": _date(status.get("primaryCompletionDateStruct")),
        "completion_date": _date(status.get("completionDateStruct")),
        "last_update_posted": _date(status.get("lastUpdatePostDateStruct")),
        "results_first_posted": _date(status.get("resultsFirstPostDateStruct")),
        "has_results": bool(study.get("hasResults")),
        "lead_sponsor": (sponsors.get("leadSponsor") or {}).get("name"),
        "collaborators": [
            c.get("name") for c in sponsors.get("collaborators", []) or [] if c.get("name")
        ],
        "conditions": conditions.get("conditions", []) or [],
        "interventions": intervention_names,
        "countries": sorted({loc.get("country") for loc in locations if loc.get("country")}),
        "url": f"https://clinicaltrials.gov/study/{ident.get('nctId')}",
    }
