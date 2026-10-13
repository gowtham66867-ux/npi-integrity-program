# Public provider-data acceptance plan

**Scope:** Live NPPES exact-NPI lookup and verification, plus the dated HHS-OIG
LEIE exact-NPI snapshot. The alert engine, claims, peers, and roster remain
synthetic. These cases do not establish fraud-detection accuracy, exclusion
clearance, credentialing, or HIPAA readiness.

Record **date, tester, environment, image/revision, source snapshot SHA-256,
observed result, evidence link/screenshot, and defect ID** for each browser
case. Use public NPI records and synthetic comparison values only; no patient
information. Automated cases run with `.venv/bin/python -m pytest -q tests`.

| ID | Priority / status | Setup and steps | Expected result |
|---|---|---|---|
| PD-01 | Critical / automated | Enter a checksum-invalid NPI in `/api/nppes`, `/api/public-integrity`, and Verify. | HTTP 400; no upstream call or partial result. |
| PD-02 | Critical / automated | Mock an exact NPPES response with both mailing and practice addresses and two taxonomies. | Practice location and primary taxonomy are selected; provider name, type, enumeration/update dates, source URL, and UTC retrieval time are retained. No alert score appears. |
| PD-03 | Critical / automated | Mock NPPES 429/timeout, empty results, wrong returned NPI, and malformed fields. | Unavailable, not found, or upstream-error state as appropriate; never “clear” or a synthetic-provider fallback. |
| PD-04 | High / automated | Verify matching name, differing city, blank state, and a license number against mocked live NPPES. | Match, Differs, Not supplied, and Cannot check yet respectively; comparison is not persisted or audited. |
| PD-05 | Critical / automated | Build LEIE index from a small fixture with one NPI row and one name-only row containing DOB and address. | Only published exact-NPI row is indexed; DOB, street address, and name-only row are absent from the packaged index. |
| PD-06 | Critical / automated | Query an NPI present in the LEIE fixture. | Result says “Published NPI match — verify with OIG”; displays source, download time, hash, row name, exclusion type/date, and official verification link; no fraud verdict. |
| PD-07 | Critical / automated | Query an absent NPI while most source rows have no NPI. | Result explicitly says “No exact NPI match in indexed LEIE rows” and “cannot clear a provider”; no “not excluded” or “safe” label. |
| PD-08 | Critical / automated | Advance clock beyond 45 days after OIG's published source-update date, then download the same file again. | Snapshot is marked stale even if the download was recent or the NPI has no indexed match; both source-update and download dates remain visible. |
| PD-09 | Critical / automated | Delete or corrupt the packaged LEIE snapshot. | HTTP 503 / “Cannot check yet”; the provider page still shows NPPES and makes no exclusion conclusion. |
| PD-10 | High / automated | Give the same NPI two LEIE rows. | Both rows remain available for human verification; neither is silently dropped or merged. |
| PD-11 | High / automated | Change required CSV headers or provide an implausibly small download. | Snapshot generation fails without replacing the prior file. |
| PD-12 | Critical / automated | Request the public-integrity endpoint on the synthetic pilot. | Response contains only the independent public LEIE check; no synthetic score, claim, alert, or case identifier. |
| PD-13 | High / browser pending | Open a public NPI with a published LEIE NPI match and compare the displayed row with OIG’s online search. | Exact NPI and published details are visible, with a prominent instruction to verify identity and current status directly at OIG before action. Record any disagreement as a defect. |
| PD-14 | High / browser pending | Open a public NPI absent from the indexed LEIE rows. | Screen says this is **not exclusion clearance**, shows indexed/total row counts and snapshot date, and links to the official OIG search. |
| PD-15 | High / browser pending | Block `/api/public-integrity/{npi}` in browser dev tools, then open the provider page. | NPPES identity remains visible; exclusion panel says “Cannot check yet” with no benign status. |
| PD-16 | High / browser pending | Reload and navigate between a live public provider and a synthetic alert case. | Live page never inherits synthetic activity, peers, case actions, or score; synthetic page stays explicitly labeled. |
| PD-17 | High / browser pending | Test 390 px width, 200% zoom, keyboard navigation, and a screen reader on the exclusion panel. | Match status, freshness warning, source link, and official verification action are perceivable without clipped text or inaccessible controls. |
| PD-18 | Critical / release gate | Before deployment, rerun sync, inspect source row counts/hash/date, run full release harness, build wheel, and inspect included snapshot. | Reproducible source provenance; tests/evals pass; built artifact contains the same index hash. A source change requires review. |
| PD-19 | Critical / deployed pending | Run billing preflight, deploy, authenticate through IAP, inspect `/api/session`, and smoke-test one NPPES and one LEIE lookup. | Correct project/account/card attestation, IAP still private, synthetic mode retained, public source panels render without backend errors. Record revision and image digest. |

## Next-source cases, not yet implemented

These are acceptance requirements for the next integration; they must not be
marked passed merely because the synthetic warehouse accepts similar columns.

| ID | Source | Test and expected result |
|---|---|---|
| NX-01 | NPPES full + weekly | Load a monthly baseline, then one weekly change; retain both dated versions and the source hashes. A repeated identical release is idempotent. |
| NX-02 | NPPES deactivation | Deactivate, then reactivate an NPI. A service date within that interval is flagged only when authorized real claims exist; a current API result cannot erase the historical interval. |
| NX-03 | LEIE identity | For name-only rows, require explicit match evidence and human verification. Similar names alone never produce a confirmed exclusion. |
| NX-04 | SAM.gov | Invalid/expired API key, rate limit, stale extract, and changed schema fail closed; source and effective dates are preserved. |
| NX-05 | PECOS / ordering / opt-out | Distinguish no public record from confirmed ineligibility; show dataset coverage, effective date, and source version. |
| NX-06 | CMS annual activity | When API/download access is verified, join by exact NPI and calendar year; show year and release lag. Missing or suppressed rows display “Unavailable,” not zero activity. |
| NX-07 | Peer comparisons | Use like-for-like taxonomy/geography/year groups with a minimum group size; show denominator and sensitivity to group definition. No anomaly is called fraud. |
| NX-08 | Real claims | Prove DUA/BAA, tenant isolation, access control, audit, retention, and no-PHI logging before ingestion. Validate service-date joins and rule precision on adjudicated labels before use. |

**Release rule:** PD-01–12 are automated gates. PD-13–19 require recorded human
execution for a deployed public-data release. NX cases remain open until their
sources and production controls exist. A passing synthetic scorecard is not a
real-world accuracy result.
