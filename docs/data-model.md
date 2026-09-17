# Data model — assertions and the golden table

## The rule
**Nothing writes to the golden table.** It is rebuilt from scratch every run as a pure function
of three versioned inputs: the assertions, `id_registry.json`, and `registry/survivorship.yaml`.
If you ever want to hand-edit a golden value, that is an assertion you have not recorded yet —
put it in `control/operator_assertions.csv` and rebuild.

## Assertions (`build/assertions.csv`, append-only in spirit)
One row per (facility, field, value, source, retrieval). Derived from every reconciled contract
row; a source row that names a plant with an address yields ~5 assertions.

| column | meaning |
|---|---|
| `facility_id` | stable IC-number from the id registry |
| `field` | golden field: name · legal_name · address · city · state · zip · lat_lon · naics · status · expiry_date · product_type |

`product_type` is asserted by the Layer 3 classifier under source `classifier`, not by any
source roster — it is the model's judgement about the row (volumetric · panel · precast ·
mass_timber · truss_component · metal_building · hud_code · other), carrying the model's own
confidence, and an `operator` correction outranks it. `legal_name` is still empty on every
release: it comes from Layer 4, which is a stub.
| `value` | verbatim, as asserted |
| `source_id` · `source_class` | who asserted it; `operator` for a human correction, `lookup` for Layer 4 |
| `retrieved_date` | when |
| `row_hash` | provenance back to the exact contract row |
| `basis` | for status: dated_expiry · explicit_status_field · certified_as_of_date · on_current_list |
| `site_visit` | assertion carries physical site-visit attestation (OSHA) |

## Survivorship (`registry/survivorship.yaml`)
Per field, an ordered preference list; first match wins; ties broken by most recent. `operator`
outranks everything; a site visit outranks a registry on address; a dated expiry outranks
"on the current list" on status. Its hash is in the release tag — a rule change is a release
change.

## Golden table (`build/golden.csv`)
One row per facility with every field and its `__source`. `n_assertions`, `n_sources` are
computed. Tier is computed from the assertion set, not stored.

## Conflicts (`build/conflicts.csv`)
Every (facility, field) with more than one distinct asserted value, the winner and why. This is
the list of things two sources disagree about — a review surface, not an error.

## What this buys
- Provenance per **field**, not per row: "where did this address come from" has an exact answer.
- A G1 merge is re-pointing assertions to one facility id; a G2 false merge unwinds the same
  way. Nothing is deleted either time.
- The Cowork prototype and the GitHub run, built from the same assertions and rules, must
  produce identical golden tables. `pipeline/compare.py` measures any gap.
