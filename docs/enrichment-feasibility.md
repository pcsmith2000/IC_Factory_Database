# Is the enrichment pass actually viable? Three probes, before anyone builds it

Written 2026-09-17. The pass itself stays deferred — this answers only whether it would work, so
the decision to greenlight it is made on evidence rather than optimism.

## Why it is the only lever left

Every roster reachable from here is ingested and swept for truncation; the control list checks out;
the T0 leads that look unparsed are genuinely unplaceable. 146 of the 239 in-scope control rows are
in **no source we hold**, and the ceiling on today's sources is 44.4%.

Adding more registries does not fix it, and that is measured rather than assumed:

    3 state registries (ca_hcd, nc_osfm, ny_dos)   529 rows  ->  2 control rows, both already held
    mbma                                            40 rows  ->  ~0
    sipa pagination fix                              4 rows  ->  3

A segment-specific manufacturer roster beats a state registry by two orders of magnitude, because
states register factory-built HOUSING and the control's gap is COMPONENTS — 36 truss rows, 12
SIP/ICF, 12 steel, 13 wood panel, 12 LGS panel, 8 mass timber, 6 bathroom pods. No state registers
a truss plant; there is no approval regime for a truss the way there is for a modular box.

And for the hardest segments there is no roster to find. The LGS and pod rows are small private
firms — Baker Triangle Prefab, TJ Wies Prefab, Atlantic PreFab, US Frame Factory, Offsitek, SurePod,
DuraPod, BathSystems. SFIA covers makers of steel framing MATERIAL, not panel fabricators, and the
US has no bathroom-pod association at all. These are reachable per company or not at all.

## The probes

Three rows, chosen across segments and difficulty, one ordinary web search each:

| row | segment | result |
|---|---|---|
| Mercer Mass Timber, Conway AR | Mass Timber | **1800 Sturgis Rd, Conway, AR 72034** — first search |
| Baker Triangle Prefab, Richardson TX | Closed LGS Panel | **1301 Apollo Road, Richardson, TX 75081** — first search |
| Dvele Modular, Mesa AZ | Closed Wood Panel | plant confirmed (220,000 sq ft) but **no street in any result** |

**Two of three resolve in a single query, including one from the hardest segment.** The third is a
miss for a specific and informative reason: Dvele's Mesa plant is recent and the coverage is trade
press announcing it rather than listing it, so the street would need the company's own site.

## What that implies for the pass

- Budget more than one query per row. A single search is roughly a two-thirds hit rate; the
  remainder needs the company website or a business directory as a second hop.
- 114 of the 146 rows carry a city AND state already, so most lookups start from a place rather
  than a bare name — which is what made the two hits cheap.
- The Mercer probe also turned up a source, not just an address: APA's certified-manufacturer
  directory carries Mercer Conway. See the apa_mills registry entry — two attempts failed on an
  XHR-backed list, and it is worth a third because APA certifies glulam, CLT and structural panel
  mills, which is 8 mass timber plus 14 wood-panel control rows.

## What this does NOT establish

That an address found this way is correct. Every probe here was read off search results, and a
street taken from a trade-press article or an aggregator is an assertion needing the same
provenance discipline as any source row — `source_url` and `retrieved_date` in the worklist exist
for that. Nothing found in this document has been written into the database.
