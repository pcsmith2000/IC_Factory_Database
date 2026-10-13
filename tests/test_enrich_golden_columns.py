from pipeline.enrich import _db


class _FakeDb:
    def __init__(self):
        self.sql = []

    def query(self, sql, params):
        self.sql.append(sql)


def test_add_golden_columns_adds_text_columns():
    db = _FakeDb()
    assert _db.add_golden_columns(db, ["capacity", "capacity__source"]) == ["capacity", "capacity__source"]
    assert db.sql == [
        'ALTER TABLE golden_facility ADD COLUMN IF NOT EXISTS "capacity" TEXT',
        'ALTER TABLE golden_facility ADD COLUMN IF NOT EXISTS "capacity__source" TEXT',
    ]
