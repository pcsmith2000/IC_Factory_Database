# v1.7 — what it should do, written before the run

Registered 2026-09-17, before dispatching, so the result can contradict it. Every figure below is
computed from run 22's 132 cached classifier batches and its normalised rows; the control list was
not consulted, and the sealed quarter of it remains the check that none of this was fitted.

## First: the v1.6 verdict, which is what sent me here

`docs/prompt-v1.5-prediction.md` closes with a falsifier — *"If v1.6 also balloons UNCERTAIN, this
diagnosis is wrong and v1.5 deserves a full run to find out why."* It fired.

Run 35259543326 (v1.6, prompt 0b4a70c2f1f3), read at batch 28 of 132 — 2,800 rows labelled, IC 464,
UNCERTAIN 194, on the same 13,057 candidates:

| quantity | v1.6 projected | 2 sd | baseline | v1.5 partial | predicted for v1.5 |
|---|---|---|---|---|---|
| UNCERTAIN | **905** | ±175 | 366 | ~829 | 403–442 |
| IC | **2,164** | ±208 | 1,969 | ~2,042 | 2,100–2,195 |

The error bars are not assumed. Run 22's 132 cached batches were resampled 4,000 times at n=28: a
28-batch projection of the full run carries sd 88 on UNCERTAIN and 104 on IC. So v1.6's UNCERTAIN
sits **6.1 sd above baseline** — no better than v1.5's, and nowhere near the predicted band.
Reordering the three tests changed nothing, because ordering was never the problem.

## Where the v1.5 diagnosis actually went wrong

It was a sizing error, and it is embarrassing in a specific way: I measured the rule's population
on the wrong population.

v1.5's hedge rule was sized at **77 rows** — the hedged reasons among the 607 core-code rows that
v1.4 had labelled NOT-IC. But the rule as written carries no such restriction. Counting hedge words
across **all 10,717 NOT-IC rows** in run 22, and splitting them by whether the record carries a core
code (321213, 321214, 321991, 321992, 332311):

    hedged NOT-IC reasons, total        927   (8.6% of all NOT-IC)
      on a core code                     81   <- what the rule was sized for
      off a core code                   846   <- what it actually reached

81 is essentially the 77 I counted. The other **846 rows were never part of the design** and are the
entire balloon: 905 observed − 366 baseline ≈ 540 rows moved, which is 64% of those 846 firing.

And the move buys nothing. An UNCERTAIN row does not publish, so a row going NOT-IC → UNCERTAIN adds
zero recall; it only grows the review queue, by 2.5x. The thing that would have helped — NOT-IC → IC
— is the +195 on the IC line, and that is a separate effect of rule 1, which v1.7 leaves alone.

## The change

Rule 3 is now explicitly confined to records carrying a core code, with an else-branch that says
what to do off them: a hedge with nothing pointing at IC is NOT-IC, and the positive finding v1.4
requires comes from the record's own NAICS code.

One more clause, added because scoping rule 3 creates a way to LOSE a plant that v1.6 could still
reach. "SOUTHERN COMPONENTS INC." in Louisiana — a control row — is coded `32121`, five digits, the
parent of the truss codes. v1.4 called it NOT-IC as "Unclear wood products", which is precisely the
hedge rule 3 is for; but under a rule that tests membership of {321213, 321214, 321991, 321992,
332311} the record is off-core, and the new else-branch would confirm the error rather than route
it to review. So a code truncated to a core family — 32121, 3212, 33231, 3323 — counts as carrying
the core code. It is coded TOWARD this dataset, not away from it.

Sized before writing it, so the clause is not doing more than it looks: of the 13,117 classified
rows, 12,353 carry a full six-digit code, 397 five and 367 four, and exactly **60** carry a parent
of a core family. Most are correctly NOT-IC (lumber yards, a coin laundry, a painting contractor),
so this clause moves a handful of rows, not a population. It is in because it is free and because
the alternative is a rule that fails on a digit count.

## The prediction

Same 13,057 candidates, same model, temperature 0. Read against the same resampled floors
(full-run noise: ±27 on net IC across four v1.4 runs).

- **UNCERTAIN falls from v1.6's ~905 to 403–447.** The ceiling is arithmetic, not judgement:
  366 baseline + all 81 core-code hedges = 447. If every core-code hedge fires it lands at 447; if
  the same ~64% fire as under v1.6, ~418. Either way it is back inside the band v1.5 was predicted
  to reach in the first place.
- **IC holds at 2,100–2,195.** v1.7 removes no IC path — rule 1 is untouched, and off core codes
  the whole "What is IC" section still governs. If IC holds here while UNCERTAIN falls, the
  widening was real all along and only its routing was broken.
- **G5 seed recall holds at or above the 85% floor.** This narrows UNCERTAIN rather than widening
  IC, so seed recall should not move; if it falls, the else-branch is eating real plants.
- **The audit floor stays near 0.5%.** A change that only demotes hedges should not admit anything.
- **Recall against the control tracks IC, not UNCERTAIN.** Expect roughly what v1.6 delivers,
  because the rows v1.7 moves were not publishing under either prompt.

## What would falsify it

**UNCERTAIN staying high** means the model is not reading "only when the record carries a core
code" either, and the problem is not the rule's wording at all — at which point the honest move is
to stop editing prose and enforce the routing in code, mapping a hedged label off a core code back
to NOT-IC in Layer 3 where it can be tested without a 30-minute run.

**IC falling below 2,100** means scoping rule 1 to core codes took away an IC path that was
carrying real plants off-code, and the else-branch is too strong. That would show up as G5 recall
falling with it, and it is the outcome I would bet against but am watching hardest, because it is
the failure that costs recall rather than merely costing review time.
