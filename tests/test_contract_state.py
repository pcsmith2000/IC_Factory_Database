from pipeline.contract import us_state


def test_a_state_is_read_never_invented():
    """`.upper()[:2]` turned Turku, Finland into Virginia and Belo Horizonte into Michigan."""
    assert us_state("WI") == "WI"
    assert us_state(" tx ") == "TX"
    assert us_state("Pennsylvania") == "PA"
    assert us_state("Varsinais-Suomi") == ""      # was VA
    assert us_state("Minas Gerais") == ""         # was MI
    assert us_state("Dubayy") == ""               # was DU
    assert us_state("AB") == ""                   # Alberta is not a US state
    assert us_state("") == "" and us_state(None) == ""
