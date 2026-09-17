# Enrichment — stages 9–13, after the warehouse load

Layers 1–8 turn published sources into a release and load it into Neon. Enrichment starts from
that loaded release and adds three fields the sources do not publish — a plant address, a rooftop
coordinate, and a building footprint — plus a fourth judgement: whether the plant still exists.

It is a separate workflow because it has a different failure profile. Layers 1–8 are pure
transformation of archived bytes and are reproducible offline; enrichment calls paid APIs, reads
the open web, and spends model tokens. Mixing them would make a quarterly release hostage to a
rate limit.

## Why five stages and not one

Each stage consumes what the previous one produced, and each can fail independently:

    9  locate     name + city (+ website)     ->  address
    10 geocode    address                     ->  lat/lon, accuracy_type
    11 footprint  rooftop lat/lon             ->  building_sqft
    12 existence  everything above            ->  existence_flag
    13 promote    every assertion of the release -> golden_facility

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

Stage 13 then runs `golden.build_golden` again and survivorship decides, exactly as for a
published source.
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
  - Within the radius it takes the **largest** building, not the nearest. The first full run made
    the reason plain: of 790 measured, 226 came back under 10,000 sqft, and those had a median of
    2 buildings within 30m against 1 for the rest — 143 of the 226 had another building beside
    them. A rooftop geocode resolves to the street address, so on a plant site the nearest building
    is the office or the guard house and the plant is the big one behind it. The nearest building's
    area is kept alongside the chosen one so the decision stays auditable.

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

That reading was wrong about stage 11, and the first workflow run proved it. 1,878 facilities carry
a coordinate, so stage 11 looked like the cheapest large win. But **every** lat_lon in the release
is asserted by `epa_frs` — 4,141 assertions, none from any other source — and EPA's coordinates are
facility-self-reported. Measured against Overture on 80 of them:

    EPA coordinates        35 of 80 resolved to a building   median 4,583 sqft
    rooftop geocodes       18 of 19 resolved                 median 63,968 sqft

An EPA coordinate lands near a site, not on its roof. That is fine for a map pin and useless for
measuring a building: 21 of the 35 it did resolve fell below the size stage 12 treats as
implausible, which would have manufactured a flag out of a geocoding artefact.

So stage 11 measures a coordinate only when a rooftop geocode produced it, and an EPA coordinate is
not a reason to skip geocoding a facility — it is the reason to geocode it. The funnel is therefore:

    stage 9  need an address                     1231
    stage 10 have address, no rooftop coordinate 2834
    stage 11 have a rooftop coordinate              0   until stage 10 runs

Stage 11 legitimately has nothing to do yet, which is a better answer than measuring 1,878 EPA
points and reporting their median as though it meant something. Stage 10's 2,834 exceed Geocodio's
2,500/day free tier, so it carries a ceiling of its own.

### What stage 11 actually measured, once stage 10 had run

1,408 rooftop coordinates, measured 2026-09-17:

    attempted                          1408
    measured                            790   56%
    deferred (20-file ceiling)          534   38%   picked up by the next run
    no building within 30m               84    6%

    median   28,751 sqft      p10   2,946
    mean     64,047 sqft      p90 144,784

The median is less than half the 63,968 sqft measured against the 30 hand-verified rows, and that
earlier figure came from 19 coordinates of which 18 resolved. n=19 was simply too small: the
distribution is heavily right-skewed, so a small sample's median says little. The scale figure is
the one to trust, and it is the reason the building selection changed from nearest to largest.

## Stage 9 was reach-limited, and search is what lifted it

Without a search tool the model cannot browse. It can only extract an address from a page that is
fetched and handed to it, which is how `corporate_locations` already works and the only form of
this that produces a citation. On that footing stage 9's reach is just the address-less facilities
for which some source publishes a URL to fetch.

    US facilities needing an address                     194 (of the registry sources; EPA aside)
    ... for which fl_bcis publishes a website             18   9%
    ... with no page to extract from                     176  91%

and only 336 of the 853 websites `fl_bcis` publishes are well-formed URLs at all.

Asking the model for an address with no page in front of it is not a smaller version of this. It
is recall from training, which is exactly what gate E1 exists to reject, and for a plant address it
would be confidently wrong often enough to poison a field no downstream stage can second-guess.

So stage 9 needed a search capability, not a better prompt, and the Vercel AI Gateway supplies one:
the Anthropic `web_search_20250305` server tool runs gateway-side, so the model both finds and reads
the page. That lifts the reach from the ~9% with a publisher-supplied URL to any facility the open
web documents.

Measured on the release database, on the 48 facilities that reached the model before the gateway
key's budget ran out:

    20  42%  located with a citation
    11  23%  the model found nothing it could cite          a reach limit
     7  15%  the cited page could not be read               Facebook, YellowPages, mystore411
     5  10%  the cited page did not contain the address     verification doing its job
     4   8%  confidence below 0.7
     1   2%  cited a page search never opened

The 15% that could not be read are sites that refuse a datacenter IP whatever User-Agent it sends,
so that bucket is a floor rather than a bug to fix.

### Which model, and how that gets decided

An earlier version of this document said Anthropic was not a free choice, because the native
`web_search` server tool is what lets the model find and open a page in one call. That was wrong.
It was a constraint of the API shape this stage happened to use, not of the gateway: Vercel's
`vercel:*_search` server tools run with **any** model it serves, and they are cheaper than the
provider-native ones. Two paths exist and `--search` picks:

    gateway   Chat Completions + vercel:parallel_search   any model       search $5/1000
    native    Messages API + web_search_20250305          Anthropic only  search $10/1000

**Search is the dominant cost, not the model.** A full pass over the 1,181 facilities that need an
address, projected at 3 searches and 30k input tokens each:

    ling-3.0-flash + tako (free to 2026-09-30)     $0.00066/facility        $0.77
    qwen3.7-flash  + tako (free to 2026-09-30)     $0.00095                 $1.12
    qwen3.7-flash  + parallel   $5/1000            $0.01595                $18.84
    qwen3.7-flash  + tako       $7/1000            $0.02195                $25.93
    sonnet-5       + anthropic $10/1000            $0.09400               $111.01

Sonnet to an open-weight model cuts the token bill ~50x and the total only 6x, because at those
prices ~90% of what remains is the per-search charge. The search provider, not the model, is the
price: $111 to $19 is mostly Anthropic's $10/1000 giving way to Parallel's $5/1000, and $19 to $1
is the Tako promotion. The model choice is the last $0.35 of it.

So `default_search()` picks by date — Tako while the promotion runs, Parallel from October 1st —
rather than by a constant someone has to remember to change. Free while free, cheapest-paid after,
no silent bill on the 1st, and the choice is recorded in every run summary.

Tako has one trap worth naming: it searches a curated data graph as well as the web and bills per
row for inlined data. Stage 9 asks for `sources.web` only and never sets `includeContents`, which
is what keeps it on the flat per-request price.

The token column is a projection; the search column is exact. Stage 9 records both, per run and per
located address, so the next real pass replaces the projection with its own numbers.

The default is `alibaba/qwen3.7-flash` with Parallel search. The task is bounded extraction behind
gates that discard anything uncited, unverified against the fetched page, or under 0.7 confidence,
so a weaker model's failures are rejected rather than stored — which makes the cheap model the one
to justify replacing, not the one to justify trying.

What the gateway path gives up: it returns no raw search results, only a call count, so the check
that the model cited a page search actually opened cannot run. That check caught 1 rejection in 48.
Fetching the cited page and confirming the address is on it — the check that does the real work —
is independent of the search path and unaffected.

Settling it is a run, not an argument, and layers 1-8 learned why: their 60-seed bake-off scored
`nova-lite` at 97/97 and production gave it recall of 20%. So the method is the same `--sample`
under each candidate, compared on cost per **located** address. The 10% whose page did not contain the address
is the number that justifies fetching the page independently at all: without that check those five
would have been stored as cited facts.

Verification is deliberately narrow, and it is worth being explicit about what it does not cover.

    verified          the cited page, fetched independently, contains the address string
    not verified      that the page is about the right company
    not verified      that the address is the plant and not a head office or a sales office
    not verified      that the page is current

The last of those is visible in the shipped data: one citation is a 2014 groundbreaking notice for a
plant "to be built". The address is real and the page says it; whether the plant still runs there
twelve years on is a different question, and stage 12 is the stage that asks it. Recency is a known,
accepted limitation of stage 9 rather than a bug in it — deferred by decision, not overlooked. The
company-identity and plant-vs-office gaps need human eyes, the way `control/VERIFY-30.csv` did; the
citations are stored in `ref_source_row`, so that is a query away whenever it is wanted.

## Stage 13 exists because assertions are not visible

Nothing downstream reads `fact_assertions`. `golden_facility` is what the site, the exports and
enrichment's own snapshot query all read, so a run that appends assertions and stops has written
rows that nobody can see. Stages 9–12 did exactly that until stage 13 existed, and a pass against
the release database would have added ~2,800 invisible rows.

Stage 13 is not a second definition of golden. It calls the same `golden.build_golden` with the
same `registry/survivorship.yaml` that layers 1–8 call, over the assertions of the same release
tag. Three things had to be true for that to mean anything:

    lat_lon        `basis:rooftop` ranks above `class:B`. Every class B coordinate is epa_frs and
                   EPA's are self-reported; without this line the rooftop geocode loses to the
                   very coordinate stage 10 exists to replace.
    address        `class:enrichment` ranks last. Worth having where no registry publishes one,
                   worth less than any registry that does.
    golden columns building_sqft and existence_flag had nowhere to land: they were not golden
                   fields, so stages 11 and 12 could not reach the table at all.

Scoped to one release tag on purpose. `fact_assertions` is append-only across releases, so
rebuilding from all of it would resurrect facilities a later release dropped — golden would stop
being a statement about the current release.

**It does not survive.** Layers 1–8 rebuild golden from the assertions they hold in memory and
`DELETE FROM golden_facility` first, so the next release drops everything stage 13 added. That is
accepted while enrichment is a separate action: the alternative is making `build_golden` a function
of the warehouse rather than of the run, which is a layers 1–8 change and belongs there, not here.
Until the two pipelines are one, an enrichment pass is re-run after a release rather than preserved
across one.

## Writing to the release database

The default is still a throwaway Neon branch, and the `workflow_run` trigger can only ever choose
that: it passes no inputs. `target: main` is a deliberate `workflow_dispatch` choice and the only
way an enrichment pass is actually published. It creates no branch, which is also what stops
cleanup having anything to delete — and `neon_branch.py delete` refuses a default branch outright,
because the failure it prevents is unrecoverable and the check costs one API call.

## Gates

Enrichment gets its own gates, in the spirit of G1-G5: they block rather than advise.

    E1  a located address must carry a citation URL and a verbatim quote
    E2  no coordinate may be published from a non-rooftop geocode
    E3  a footprint must name the Overture building id it was measured from
    E4  no stage may reduce the count of facilities with an address or a coordinate
    E5  existence flags are advisory: the stage may never delete or unpublish a facility
    E6  the rebuilt golden must cover every field at least as well as the one it replaces

E4 is the important one. Enrichment only ever adds; if a run would take a field away from a
facility that had it, something upstream broke and the run halts.

E6 is E4 asked of the table rather than of the stage outputs. Stage 13 is the one stage that can
destroy a release rather than merely fail to improve it, so it compares per-field coverage counted
in the database — not in the eight-column snapshot the stages use, which would read zero for every
field it does not select and wave the rebuild through.

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
