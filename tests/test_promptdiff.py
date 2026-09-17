"""The check that sees what G5 cannot.

On 2026-09-17 prompt v1.1 -> v1.2 raised G5 precision 96% -> 100% and held recall at 87%, while
dropping ~90 genuine core-code plants. Both facts were true at once, because the 60 seeds contain
neither a plywood mill nor a CMH plant. These tests pin the arithmetic that surfaced it.
"""
from pipeline import promptdiff


def _lab(rows):
    return {h: {"row_hash": h, "label": l, "reason": r} for h, l, r in rows}


def test_counts_every_direction_a_label_can_move():
    old = _lab([("a", "IC", ""), ("b", "NOT-IC", ""), ("c", "UNCERTAIN", ""), ("d", "IC", "")])
    new = _lab([("a", "NOT-IC", "x"), ("b", "IC", ""), ("c", "IC", ""), ("d", "IC", "")])
    d = promptdiff.diff(old, new)
    assert d["moves"] == {"IC -> NOT-IC": 1, "NOT-IC -> IC": 1, "UNCERTAIN -> IC": 1}
    assert len(d["dropped"]) == 1 and len(d["gained"]) == 2
    assert d["rows_compared"] == 4          # "d" did not move and is still compared


def test_separates_an_intended_drop_from_a_candidate_loss():
    """A millwork row leaving is the point of the change; a manufactured-home row leaving is not."""
    old = _lab([("m", "IC", ""), ("h", "IC", "")])
    new = _lab([("m", "NOT-IC", "millwork"), ("h", "NOT-IC", "vague")])
    names = {"m": ("DOVER MILLWORK INC", "321918"), "h": ("CMH MANUFACTURING, INC.", "321991")}
    d = promptdiff.diff(old, new, names)
    assert d["product_naics_dropped"] == 1
    assert d["core_naics_dropped"] == 1
    assert d["core_net"] == -1
    assert d["core_by_code"]["321991"]["lost"] == 1
    # the loss must be nameable, with the model's own reason, or nobody can check it
    loss = [x for x in d["dropped"] if x["naics"] == "321991"][0]
    assert loss["name"] == "CMH MANUFACTURING, INC." and loss["reason"] == "vague"


def test_core_net_nets_off_rows_regained():
    old = _lab([("x", "IC", ""), ("y", "NOT-IC", "")])
    new = _lab([("x", "NOT-IC", ""), ("y", "IC", "")])
    names = {"x": ("A TRUSS CO", "321214"), "y": ("B TRUSS CO", "321214")}
    d = promptdiff.diff(old, new, names)
    assert d["core_naics_dropped"] == 1 and d["core_naics_gained"] == 1
    assert d["core_net"] == 0


def test_rows_only_one_side_knows_are_ignored():
    d = promptdiff.diff(_lab([("a", "IC", "")]), _lab([("a", "IC", ""), ("z", "IC", "")]))
    assert d["rows_compared"] == 1


def test_render_names_the_losses():
    old = _lab([("h", "IC", "")])
    new = _lab([("h", "NOT-IC", "Manufacturing vague likely not IC.")])
    out = promptdiff.render(promptdiff.diff(old, new, {"h": ("CMH MANUFACTURING, INC.", "321991")}))
    assert "CMH MANUFACTURING" in out and "net -1" in out
