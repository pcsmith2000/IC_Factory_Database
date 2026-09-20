# Tako basic search pilot — 20 September 2026

Final [live run](https://github.com/pcsmith2000/IC_Factory_Database/actions/runs/35538672256) completed 10/10 rows with confirmed Tako searches and passed the development quality gates. Gemini 3.1 Flash-Lite was used for search and extraction. **No database writes.**

Pre-run estimate: **$0.15** (range $0.075–$0.30, not a cap). Actual gateway-reported cost: **$0.063792**, including 10 confirmed Tako calls under the current free-search promotion. The earlier Haiku/Sonnet run cost $0.652002, so this completed run cost about 90% less. Token counts and per-run comparisons are in `pilot-report.json`.

## Findings

- Seven correct websites and six correct phone numbers among nine resolvable references. The phone count includes corroboration of an existing number; it is not six new fills.
- All three known identity/location hazards were held for review: Hart Housing, Vulcraft’s VA/UT mismatch, and Canam’s NY/NJ mismatch.
- 30/39 reference checks passed: 27 supplied field values matched and three hazard checks passed. Nine checks lacked a returned value; those are coverage gaps, not wrong values.
- Murphy and Hoover returned no usable contact evidence to the fetcher. Their known reference answers were not injected into the researcher.
- nVent/Avail ownership and contact changes remain review material; FPEC was conservatively flagged despite matching published contact details.
- Candidate fields were manually checked against the independent reference. Review values, including historical Hart contact details, are not approved for assertions.

## Per-row results

| Facility | Website found | Phone found | Disposition |
|---|---|---|---|
| MARICORP US, LLC | https://maricorp.com | 877-858-3625 | matched |
| WA317936037 - MURPHY COMPANY DBA MURPHY COMPANY OF OREGON | Unverified | Unverified | review |
| HART HOUSING GROUP INC PLANT 1 | https://www.harthousing.com | (574) 862-4461 | conflict |
| Vulcraft | https://vulcraft.com | (435) 734-9433 | conflict |
| Hoover Trusses | Unverified | Unverified | review |
| F.P.E.C CORPORATION OF ARKANSAS, INC | https://fpec.com | (479)-751-9392 | conflict |
| Canam Steel Corporation | https://cscsteelusa.com/ | 908-561-3484 | conflict |
| Southern Storage Solutions, Inc. | https://www.southernstorage.org/ | 850-536-5843 | matched |
| AIS - Millington | https://nvent.com | Unverified | matched |
| T & M MANUFACTURING, INC. | https://www.tmmfg.com | 435-257-1400 | matched |

## Validation

21 targeted tests and full repository CI pass. A live `plan` run with `states=MO`, `missing_fields=website,phone`, `row_limit=1` selected one of 99 eligible rows with database read-only protection enabled.

Seven research iterations tested execution, evidence grounding, contact parsing, and lower-cost models. Qwen did not confirm search calls; Gemini 2.5 Flash-Lite produced a malformed search call; both were rejected. The final 3.1 run completed successfully. Manual-only workflow dispatch prevents code pushes from starting further paid tests.

This is a ten-row development pilot, not production-wide accuracy evidence. Database writing remains deliberately unavailable.
