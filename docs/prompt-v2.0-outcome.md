# v2.0 — structural steel fabricators, predicted and then measured

Registered in the prompt's own changelog before dispatch; measured here afterwards. Prompt hash
`3c9f6cb0ba1a`, run [35640941830](https://github.com/pcsmith2000/IC_Factory_Database/actions/runs/35640941830),
release `v1.0.0+reg.d0c18c1+ids.23d47f52+ctl.1c9a2dba+surv.96068b3c+prompt.3c9f6cb0ba1a`.

This file exists so the correction below does not have to be made in the frozen prompt. Editing
that file moves its SHA-256, and the hash is in every release tag: a one-line fix to a changelog
would invalidate the classify cache and buy a second 245-batch, 50-minute re-classify for nothing.
The prompt records what was predicted; this records what happened.

## The prediction, and a correction to it

ADL ruled on 2026-09-21 that a shop cutting and welding structural steel to someone else's
drawings is a trade, not a building system. The rule was written on the PRODUCT rather than on
NAICS 332312, because that code is shared: dropping it wholesale would have deleted NCI Building
Systems, Ceco, Schulte, Liberty, Jedco, Structall, Kingspan, New Millennium, Valley Joist, CMC
Joist & Deck and American Modular Systems along with the fabricators.

The changelog predicted **~79 rows out, none in**. That figure was measured on the wrong release —
an older build holding 5,308 golden rows and 111 at 332312. The release this ran against held
**6,531 rows and 193 at 332312**, so the corrected prediction, registered before the run finished,
was **~152 out**.

## What happened

| | before (r42) | after (r43) | |
|---|---|---|---|
| golden rows | 6,531 | 6,426 | −105 net |
| NAICS 332312 | 193 | 99 | **−94** |
| 332312, IC-named | 41 | **41** | **none lost** |
| trade-named across all codes | 311 | 163 | **−148** |

Gate G5 re-passed on the 60 seeds: precision 94% (min 90), recall 100% (min 85).

**The protected side held completely.** Every one of the 41 building-system, joist, deck, panel and
modular names at 332312 is still in the release. That was the risk the product-based wording was
written to avoid and it did not materialise. CIVES STEEL, the case that prompted the ruling, is
gone.

The net golden figure is −105 rather than −148 because a run moves rows both ways: 112 dedupe
collapses and newly admitted rows land in the same number. −94 and −148 are the measurements; −105
is an aggregate that cannot be read as this rule's effect.

## Where it is incomplete

**163 trade-named facilities remain**, and they sit where the rule cannot reach:

| where | n | why it survives |
|---|---|---|
| no NAICS at all | 45 | the classifier had only a name; Banker Steel's 15 rows are here |
| NAICS 332311 | 65 | the code says prefabricated metal building and argues against the name |
| NAICS 332312 | 46 | 23 of these are the trade shops the rule missed |

Banker Steel is the clean miss: a pure structural fabricator at 15 addresses, most rows carrying no
code. `registry/sources.yaml` already has a `not_manufacturers` list that would take it by name,
which is a cheaper instrument than another prompt version.

## One name cited wrongly

CANAM STEEL CORPORATION was named alongside CIVES STEEL as an example of what should be dropped.
That was wrong. Canam makes open-web steel joists and steel deck, which is **Light Gauge Steel
Structural Components** under ADL's taxonomy — in scope. Its eleven rows survived and should have.
The real fault there was enrichment stage 15 labelling it Pre-Engineered Metal Building at 0.8
confidence, which is a capability error and not a scope one.
