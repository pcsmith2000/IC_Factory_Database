# Web research pipeline evaluation: final report (2026-09-26)

Stopped by the user after gate 1 (no second gate). Spend: **$3.46 list, $1.35 billed** of the $10.
Nothing was written to the warehouse.

## Winning configuration: v9 (`configs/v9-qwen-veto.json`)

- Crawl the known website, plus sibling-company sites found in golden (free); one Tako search only when no page
  anchors the plant (search via `gemini-3.1-flash-lite`, confirmed by the gateway).
- Judge: `alibaba/qwen3.7-flash` (open weight, $0.03/$0.13 per M), reasoning low, 6k-token passages, strict-site
  rule, confirmed-fields prompt; fallback `gpt-oss-120b` on content-filter refusals.
- Findings: verbatim quote check, pre-contract validation, same-domain regex emails, website from an anchored
  company page.
- Removals: closed only on two pages; not_ic only if `gpt-oss-120b` independently agrees AND no in-scope product
  word appears in the passages (user's decisions); address guard; capability guard. Duplicates are proposals.

## Holdout (gate 1, see gate1.md)

S1 0 (holdout and 50 ADL anchors), S2 0, S3 0, F1 0.98, F2 0.73, C1 $0.0078 list: pass.
V1 0.72 and V2 0.68: fail (V2 removing waived). 4.8 literal facts per facility against the reference's 2.4;
novel-fact precision 0.87.

## Projection

List price $0.0078 per facility, so about **$35 for the ~4,500 facilities wr-full-1 has not researched**
(about $10 billed while Tako search is free, through 2026-09-30). Runner time ~0.43 min per facility.

## Recommendation

Run it for literal facts (contacts, addresses, websites) in growing batches with a spot-check between batches,
starting at 25-50; route every removal to human review. See LEARNINGS.md for what was tried and why.
