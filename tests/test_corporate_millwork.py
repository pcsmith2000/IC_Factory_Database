from pipeline.sources.corporate_locations import _is_millwork_only


def test_a_door_shop_is_not_a_plant_but_a_truss_plant_with_a_mill_shop_is():
    assert _is_millwork_only("Door Shop")
    assert _is_millwork_only("Doors-Millwork")
    assert _is_millwork_only("Millwork Locations")
    assert not _is_millwork_only("Truss Plant")
    assert not _is_millwork_only("Trusses, Wall Panels and Components Doors & Millwork Windows Business Center Manufacturing Facility")
    assert not _is_millwork_only("")          # unknown kind is not a reason to drop
