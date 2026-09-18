"""Carry authenticated employee evidence through release rebuilds.

The web writer stores the original note, a provenance anchor and field assertions in
one transaction. Only assertions joined to that ledger qualify for human precedence.
"""
SOURCE = 'adl_employee_feedback'


def carry_forward(warehouse, cursor, assertions, facilities):
    raw = cursor.execute('''SELECT DISTINCT a.assertion_id, a.facility_key, a.field_key, a.value,
        a.date_key, a.row_hash, a.asserted_at, e.creates_facility
        FROM fact_assertions a JOIN employee_feedback e ON e.feedback_id=a.row_hash
        WHERE a.source_key=?''', (SOURCE,))
    saved = warehouse._rows(raw)
    active = {f['facility_id'] for f in facilities}
    created = {a['facility_key'] for a in saved if a['creates_facility']}
    result = list(assertions)
    seen = set()
    for a in saved:
        if a['facility_key'] not in active | created or a['assertion_id'] in seen:
            continue
        seen.add(a['assertion_id'])
        result.append(dict(facility_id=a['facility_key'], field=a['field_key'], value=a['value'],
            source_id=SOURCE, source_class='human_feedback', retrieved_date=a['date_key'],
            row_hash=a['row_hash'], asserted_at=a['asserted_at'], basis='human_verified',
            site_visit=False, confidence=None, assertion_id=a['assertion_id']))
    # Employee-created records have no external roster yet. Preserve their stable IDs.
    facilities = list(facilities)
    for fid in sorted(created - active):
        fields = {a['field']: a['value'] for a in sorted(result, key=lambda x: x.get('asserted_at') or '') if a['facility_id']==fid}
        facilities.append(dict(facility_id=fid, name=fields.get('name'), state=fields.get('state'), tier='T1'))
    return result, facilities
