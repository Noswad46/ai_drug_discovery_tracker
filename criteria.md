# AI-designed drugs seed list: inclusion criteria

Companion to `seed-list.csv`. Last checked: 19 November 2026.

## What counts as "AI-designed"

A drug is included when **AI or machine learning was used materially in at least one of**:

1. **Target identification**: the biological target or mechanism was nominated by an AI/ML platform (e.g. Insilico's PandaOmics nominating TNIK for IPF; Recursion's phenomics maps).
2. **Molecule design or optimisation**: the chemical or biological structure was generated, prioritised or optimised by AI/ML or large-scale computational physics models (e.g. generative chemistry, protein language models, free-energy-perturbation design at scale).
3. **Repurposing**: an existing molecule was matched to a new indication by an AI/ML system, *and* the company states this (e.g. BenevolentAI and baricitinib for COVID-19). These are tagged `ai_role = repurposing` so the dashboard can include or exclude them.
4. **Personalised design**: the product is designed per patient by an algorithm (e.g. AI-selected neoantigens in cancer vaccines). Tagged `ai_role = personalised_design`.

Excluded: AI used only in trial operations (site selection, recruitment, digital twins), patient stratification/biomarkers on a conventionally discovered drug (e.g. Alto Neuroscience), or in-licensed assets that an AI-branded company did not design (e.g. most of Formation Bio's and Pathos's in-licensed pipeline).

**Must be clinical-stage**: at least one human dose in an interventional trial (Phase 1 onwards), including programmes since discontinued, so the dashboard can track attrition as well as progress.

## Confidence column

| Level | Meaning |
|---|---|
| **high** | The company or a peer-reviewed publication explicitly states AI/ML designed the molecule or nominated the target. |
| **medium** | Asset from an AI-native or computation-first company (or AI repurposing) where AI use is likely but the specific claim for this molecule is not explicit, or the "AI" is mainly physics-based computation (e.g. Schrödinger, Nimbus, Relay). |
| **low** | Only a partnership announcement, press coverage or third-party tracker links the asset to AI; no company or paper claim found. |

Flag uncertain entries instead of dropping them: the `status` or `ai_role` notes say what still needs checking.

## Column definitions

| Column | Meaning |
|---|---|
| drug_name | INN if assigned, else primary code |
| alt_codes | Other codes, separated by `;` |
| company | Originator / current owner |
| partner | Licensee or co-developer, if any |
| ai_platform | Named platform (e.g. Pharma.AI/Chemistry42, Recursion OS, LiveDesign/FEP+) |
| modality | small molecule, antibody, peptide, vaccine, etc. |
| target | Molecular target |
| indication | Lead indication |
| therapy_area | oncology, immunology/inflammation, fibrosis, metabolic, CNS, infectious, rare/genetic, ophthalmology, cardiovascular, other |
| highest_phase | Highest phase reached: Phase 1, Phase 1/2, Phase 2, Phase 2/3, Phase 3, Filed, Approved |
| status | active, completed, discontinued, deprioritised, unclear (with short note) |
| first_in_human_date | YYYY-MM or YYYY where known; blank if unknown |
| nct_ids | ClinicalTrials.gov IDs, `;`-separated (Chinese CTR/CTR20 IDs where no NCT) |
| ai_role | target_id, molecule_design, both, repurposing, personalised_design (plus short note) |
| confidence | high / medium / low (see above) |
| source_url | Best primary source checked |
| last_checked | Date the row was last verified |

## Sources used

- Jayatunga et al., "How successful are AI-discovered drugs in clinical trials?", *Drug Discovery Today* 2024 (BCG/Wellcome), [PubMed 38692505](https://pubmed.ncbi.nlm.nih.gov/38692505/): defines AI-native biotech assets; 75 molecules entering clinic 2015–2023.
- ASCO 2026 abstract 11072 (*JCO* 44:16 suppl), [doi](https://doi.org/10.1200/jco.2026.44.16_suppl.11072): 117 AI-enabled assets from 63 companies in interventional trials by Dec 2025.
- Trackers: [IntuitionLabs](https://intuitionlabs.ai/articles/ai-discovered-drugs-clinical-trials-2026), [Medspark](https://medspark.ai/2026/07/19/over-173-ai-designed-drugs-now-in-trials-as-fda-decision-nears/), [Vision Lifesciences](https://visionlifesciences.com/insights/ai-drug-discovery-companies).
- Company pipeline pages and press releases (linked per row) and ClinicalTrials.gov for NCT IDs.

## Known gaps

- The ASCO and Medspark totals (117 to ~175) are larger than this list because they include many Chinese and undisclosed-code assets whose AI claims could not be checked. Treat this seed list as the *verifiable core*, and grow it from tracker updates.
- **NCT IDs are sparse.** ClinicalTrials.gov blocks automated lookups from the environment this list was built in, so only IDs confirmed from press releases or trackers are filled. Blank `nct_ids` means "not yet verified", not "no trial". The first dashboard refresh should query the ClinicalTrials.gov API (v2, `query.intr=<drug code>`) for every row.
- **32 rows have status `unclear`**, mostly smaller companies or older Insilico/Exscientia assets whose latest phase was not confirmed today. They carry a `VERIFY` note.
- **Edge cases kept but tagged** so the dashboard can filter them: AI repurposing of known molecules (baricitinib, REC-4881, REC-994, REC-2282, BXCL501), physics-based computational design (Schrödinger, Nimbus, Relay, Structure), and algorithmic neoantigen vaccines (EVX-01, TG4050, mRNA-4157).

## Watchlist (not yet clinical, likely to enter soon)

- Iambic IAM217 (KIF18A): Phase 1/2 launching; IND submitted Sep 2026.
- Iambic IAM-C1 (CDK2/4): IND planned Q4 2026.
- Structure ACCG-3535 (amylin/calcitonin): Phase 1 guided Q4 2026.
- Recursion/Sanofi first optioned immunology candidate: Phase 1 expected 2027.
- Isomorphic Labs: first clinical trials guided for end 2026, no molecule disclosed.
- Schrödinger SGR-6016 (NLRP3): development candidate.
