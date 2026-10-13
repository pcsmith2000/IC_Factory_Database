"""pipeline/feedback.carry_forward without a database: a release rebuild must carry every saved
employee edit forward, keep its original assertion_id, and keep a plant an employee created."""
from pipeline import feedback, warehouse


class Cursor:
    def __init__(self, saved):
        self.saved, self.calls = saved, []
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self.saved


class Warehouse:
    @staticmethod
    def _rows(raw):
        return [dict(r) for r in raw]


def saved(fid="IC-1", field="name", value="Employee name", aid="fb-1", at="2026-09-18T12:00:00Z", created=0, row="feedback-1"):
    return dict(assertion_id=aid, facility_key=fid, field_key=field, value=value, date_key=at[:10],
                row_hash=row, asserted_at=at, creates_facility=created)


def registry(fid="IC-1", value="Registry name"):
    return dict(facility_id=fid, field="name", value=value, source_id="state_registry", source_class="A")


def test_an_employee_edit_is_carried_into_the_new_release_as_saved():
    c = Cursor([saved()])
    out, facilities = feedback.carry_forward(Warehouse(), c, [registry()], [dict(facility_id="IC-1")])
    assert out[0] == registry()                                    # the release's own facts untouched
    carried = out[1]
    assert carried == dict(facility_id="IC-1", field="name", value="Employee name", source_id="adl_employee_feedback",
                           source_class="human_feedback", retrieved_date="2026-09-18", row_hash="feedback-1",
                           asserted_at="2026-09-18T12:00:00Z", basis="human_verified", site_visit=False,
                           confidence=None, assertion_id="fb-1")
    assert facilities == [dict(facility_id="IC-1")]
    # only assertions joined to the employee_feedback ledger, for the employee source
    sql, params = c.calls[0]
    assert "JOIN employee_feedback" in sql and params == ("adl_employee_feedback",)


def test_the_carried_edit_keeps_its_original_assertion_id():
    """warehouse.assertion_id honours an employee assertion's own id, so loading the same release
    twice (or a later one) never appends a second copy of the edit."""
    out, _ = feedback.carry_forward(Warehouse(), Cursor([saved(aid="fb-7")]), [], [dict(facility_id="IC-1")])
    assert warehouse.assertion_id(out[0]) == "fb-7"


def test_every_edit_is_carried_once_even_when_the_join_repeats_it():
    rows = [saved(), saved(), saved(field="city", value="Dayton", aid="fb-2")]
    out, _ = feedback.carry_forward(Warehouse(), Cursor(rows), [], [dict(facility_id="IC-1")])
    assert sorted(a["assertion_id"] for a in out) == ["fb-1", "fb-2"]


def test_an_edit_to_a_plant_the_release_dropped_is_not_resurrected():
    out, facilities = feedback.carry_forward(Warehouse(), Cursor([saved(fid="IC-9")]), [], [dict(facility_id="IC-1")])
    assert out == [] and facilities == [dict(facility_id="IC-1")]


def test_a_plant_an_employee_created_survives_with_its_latest_name_and_state():
    rows = [saved(fid="IC-96900", value="Old name", aid="a", at="2026-10-01T00:00:00Z", created=1),
            saved(fid="IC-96900", value="New name", aid="b", at="2026-10-02T00:00:00Z", created=1),
            saved(fid="IC-96900", field="state", value="OH", aid="c", created=1)]
    out, facilities = feedback.carry_forward(Warehouse(), Cursor(rows), [], [])
    assert {a["assertion_id"] for a in out} == {"a", "b", "c"}
    assert facilities == [dict(facility_id="IC-96900", name="New name", state="OH", tier="T1")]


def test_a_created_plant_the_release_already_lists_is_not_added_twice():
    out, facilities = feedback.carry_forward(Warehouse(), Cursor([saved(created=1)]), [], [dict(facility_id="IC-1")])
    assert len(out) == 1 and facilities == [dict(facility_id="IC-1")]


def test_the_inputs_are_not_mutated():
    assertions, facilities = [registry()], [dict(facility_id="IC-1")]
    feedback.carry_forward(Warehouse(), Cursor([saved(fid="IC-2", created=1)]), assertions, facilities)
    assert assertions == [registry()] and facilities == [dict(facility_id="IC-1")]
