# Enrichment — stages 9–12, after the warehouse load

Layers 1–8 turn published sources into a release and load it into Neon. Enrichment starts from
that loaded release and adds three fields the sources do not publish — a plant address, a rooftop
coordinate, and a building footprint — plus a fourth judgement: whether the plant still exists.

It is a separate workflow because it has a different failure profile. Layers 1–8 are pure
transformation of archived bytes and are reproducible offline; enrichment calls paid APIs, reads
the open web, and spends model tokens. Mixing them would make a quarterly release hostage to a
rate limit.

## Why four stages and not one

Each stage consumes what the previous one produced, and each can fail independently:

    9  locate     name + city (+ website)     ->  address
    10 geocode    address                     ->  lat/lon, accuracy_type
    11 footprint  rooftop lat/lon             ->  building_sqft
    12 existence  everything above            ->  existence_flag

A facility can enter at any stage. One that already has a street address skips 9. One that already
has a rooftop coordinate skips 9 and 10. Stage 11 only ever sees coordinates stage 10 was willing
to publish. Re-running stage 11 alone after an Overture release must not re-run the model in 9.

Each stage is a separate workflow job with `needs:` on the one before it, and a separate module
under `pipeline/enrich/`. Every stage is idempotent: it selects the facilities missing its output
field, and writing the same release twice changes nothing.

## Everything written is an assertion

No stage writes to `golden_facility`. Each appends to `fact_assertions` under its own source id:

    enrich:locate      address       a plant address found and cited by the model
    geocode:geocodio   lat_lon       a coordinate, with its accuracy_type
    overture:building  building_sqft a footprint area, with the building id it came from
    enrich:existence   status        a dead-plant flag, never a deletion

`golden.build_golden` then runs again and survivorship decides, exactly as for a published source.
This keeps `v_provenance` answering "why is this facility here, and who says so" for a geocoded
coordinate as readily as for a state licence — and it means `operator` (a human correction in
`control/operator_assertions.csv`) still outranks every one of these, as `survivorship.yaml` says.

## What each stage may and may not do

**9 — locate.** For facilities with a name, city and state but no street address. The model is
given the company name, the city, and the manufacturer's own website where a source published one
(`fl_bcis` publishes 853 of them). It must return a street address **and** the URL it came from
with the address quoted verbatim from that page. An address without a citation is discarded, not
stored. The model is never asked to recall an address from training.

**10 — geocode.** Geocodio batch, address to coordinate. Measured on 200 of our own addresses,
rooftop coverage is 81.6% and the accuracy types partition cleanly by source: every rooftop came
from a local parcel or address-point file, every non-rooftop from TIGER/Line. Human verification of
30 of them (`control/VERIFY-30.csv`) then showed the types fail differently:

    rooftop                80% verified   failures are the right site, point off by 25-110m
    nearest_rooftop_match  30% verified   failures are a different parcel: a vacant lot, a house, a road

So only `rooftop` is stored as a coordinate. `nearest_rooftop_match`, `range_interpolation`,
`street_center` and `place` are recorded as a geocode-quality flag and nothing else — a coordinate
on the wrong parcel is worse than no coordinate, because it will be measured in stage 11 and
rendered on the map as though it were known.

**11 — footprint.** The building polygon under the coordinate, from Overture buildings on S3, and
its area. Two cautions the verification set already raised:

  - A rooftop coordinate can sit a metre or so *outside* the polygon, so the match is
    nearest-building-within-tolerance, not strict containment.
  - A plant is often a multi-building campus (Madison Industries) and for precast operations the
    working area is an open yard with no roof at all (Concrete Modular Systems). Stage 11 therefore
    reports the area of the building it matched and the count of buildings on the parcel, and never
    claims to have measured "the plant".

  DuckDB's `ST_Area_Spheroid` must not be used: it ignores the cosine-of-latitude convergence of
  meridians and returns the same area for the same polygon at every latitude, correct only at the
  equator and 2x too large by 60N. Area is computed from the ring coordinates directly.

**12 — existence.** Flags, never deletions. A plant whose licence expired years ago, whose website
is gone, and under whose rooftop coordinate there is no building, is a candidate for retirement —
but the call is a human's. The stage writes the evidence and the flag; `control/operator_assertions.csv`
is where a decision gets recorded.

## The funnel, measured against the live release

Read from the release database on 2026-09-17 (`v1.0.0+reg.22389a4`), not estimated:

    total facilities                        4065
    stage 9  need an address                1231
    stage 10 have address, need coordinate   956
    stage 11 have a coordinate to measure   1878
    neither address nor coordinate          1231

Stage 11 is the one with work to do today: 1,878 facilities already carry a coordinate, almost all
of them from EPA, and none of them has a footprint. Stage 10's 956 fit inside Geocodio's free tier
over two days. Stage 9's 1,231 are the hard part, and the number that matters there is not 1,231
but how many can be reached at all — see below.

## Stage 9 reaches far less than its target, and the reason is structural

The model cannot browse. It can only extract an address from a page that is fetched and handed to
it, which is how `corporate_locations` already works and the only form of this that produces a
citation. So stage 9's real reach is the number of address-less facilities for which some source
publishes a URL to fetch.

    US facilities needing an address                     194 (of the registry sources; EPA aside)
    ... for which fl_bcis publishes a website             18   9%
    ... with no page to extract from                     176  91%

and only 336 of the 853 websites `fl_bcis` publishes are well-formed URLs at all.

Asking the model for an address with no page in front of it is not a smaller version of this. It
is recall from training, which is exactly what gate E1 exists to reject, and for a plant address it
would be confidently wrong often enough to poison a field no downstream stage can second-guess.

So stage 9 ships covering the ~9% it can cite, and the remaining 91% stays an open question whose
answer is a search capability, not a better prompt. Whether the Vercel AI Gateway exposes a web
search tool decides it, and that is untested.

## Gates

Enrichment gets its own gates, in the spirit of G1-G5: they block rather than advise.

    E1  a located address must carry a citation URL and a verbatim quote
    E2  no coordinate may be published from a non-rooftop geocode
    E3  a footprint must name the Overture building id it was measured from
    E4  no stage may reduce the count of facilities with an address or a coordinate
    E5  existence flags are advisory: the stage may never delete or unpublish a facility

E4 is the important one. Enrichment only ever adds; if a run would take a field away from a
facility that had it, something upstream broke and the run halts.

## Deconfliction — enrichment runs beside an active layers 1–8

Layers 1–8 and enrichment are worked on concurrently, so enrichment must never assume it is the
only writer, and must never be the reason a 1–8 run is disturbed.

**Production: a Neon branch, not the release database.** The workflow creates a Neon branch from
the release database, runs stages 9–12 against the branch, and opens a PR with the enrichment
release. A 1–8 run publishing while enrichment is mid-flight therefore cannot collide with it, and
a failed enrichment leaves the release database untouched. The branch is deleted when the PR closes.

**Development: a frozen sample, not live Neon.** Stages are built against a snapshot pinned to one
release tag, so a new 1–8 run landing mid-iteration does not move the ground. `--sample N` takes a
deterministic, state-stratified subset; every stage honours it, so a full design/run/inspect cycle
costs a few dozen API calls rather than a few thousand.

**Ownership.** Enrichment owns `pipeline/enrich/`, `.github/workflows/enrich.yml`,
`tests/test_enrich.py` and this file. It does not modify layers 1–8, their sources, or their gates;
anything it needs from them is a finding handed over, not an edit made in passing.

**Shared budgets.** The Geocodio key is one free tier of 2,500 lookups a day across everyone using
it. Stage 10 records its own daily spend in the run record, and `--sample` exists so that iterating
on the design does not consume the allowance a real run needs.
