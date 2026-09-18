# ADL employee feedback

Companion UI: `pcsmith2000/ADL_Viz`, branch `feat/adl-employee-feedback`.

## Storage and precedence

`employee_feedback` is an append-only submission ledger, including original note,
self-reported employee name, shared-passcode authentication method, entry channel,
changed fields and their prior values, server timestamp, idempotency hash and whether
this submission creates a new physical facility. `ref_source_row` anchors the submission
UUID to that note. Each confirmed field is a `fact_assertions` row under the registered
source `adl_employee_feedback` and source class `human_feedback`.

Survivorship version 3 ranks ADL employee feedback first for every golden field, ahead
of the existing CSV operator corrections. Among employee assertions, the latest server
timestamp wins; a submission key breaks any exact timestamp tie deterministically.
Human verification does not imply independent corroboration or a site visit. Historical
assertions are never deleted when a newer human correction wins.

## Migration and publication

On PostgreSQL, `python -m pipeline.warehouse init` applies the idempotent
`pipeline/migrations/001_employee_feedback.sql` after existing tables and views are initialized.
Apply it to an isolated preview database first. Existing initialization also creates the
ledger for SQLite, which supports release carry-forward tests; the web writer requires
Postgres and the installed trigger. The migration adds no sample submissions.

`preserve_employee_feedback` reapplies ledger-backed assertions on every golden
INSERT/UPDATE, carries the full employee history into the new release, and refreshes
assertion/source totals. It protects corrections from enrichment snapshots taken before
an employee saved them. `replace_golden_with_feedback` provides atomic promotion over
Neon HTTP, rejects obsolete release tags, and preserves employee-created facilities
absent from a stale snapshot. The matching enrichment code uses this function when
installed, with the existing behavior retained for unmigrated warehouses.

The base loader acquires the same advisory lock as the web writer, reads authenticated
feedback from the ledger, then recomputes golden and conflicts with current survivorship
rules. Each historical assertion is carried once per release using its original assertion
ID and provenance. External facilities dropped by the current release stay dropped;
explicit employee-created facilities retain their `ADL-<UUID>` key and remain published.
Deploy this loader before enabling the web feature; old loaders cannot preserve new
employee-created records that never appeared in their external roster.

All writers should use the matching branch. Never run an old publisher concurrently with
the migration or the enabled feature. Do not change source keys or mutate feedback ledger
rows to correct a value; submit a new assertion through the employee UI.

## Tests

`python -m pytest -q tests/test_employee_feedback.py tests/test_warehouse.py
 tests/test_golden_optional_fields.py tests/test_reconcile_gates.py`

The companion ADL VIZ `npm run test:feedback` exercises this migration in embedded
PostgreSQL, including trigger carry-forward and the atomic promotion function. Its
Mercury responses are test fixtures. Live Neon/Mercury acceptance requires configured
preview credentials, which are not committed to either repository.

Observations without a matching structured field are proposed as `employee_notes`.
They use the same confirmation, source, assertion and golden-field path; earlier notes
remain in assertion history when a newer note becomes the selected value.
