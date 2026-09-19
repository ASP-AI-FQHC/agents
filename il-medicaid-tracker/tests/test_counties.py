from tracker.counties import COUNTIES, canonical


def test_exactly_102_counties():
    assert len(COUNTIES) == 102
    assert len(set(COUNTIES)) == 102


def test_matches_multiword_and_spacing_variants():
    assert canonical("De Witt") == "DeWitt"
    assert canonical("DeKalb") == "DeKalb"
    assert canonical("Jo Daviess") == "JoDaviess"
    assert canonical("La Salle") == "LaSalle"
    assert canonical("St. Clair") == "StClair"
    assert canonical("Rock Island") == "RockIsland"


def test_rejects_non_counties():
    assert canonical("Grand Total") is None
    assert canonical("Aetna Better Health") is None


def test_recovers_ligature_damaged_names():
    """2018-2020 PDFs mangle these; dropping them loses ~28,000 enrollees/month."""
    from tracker.counties import canonical_fuzzy
    assert canonical_fuzzy("Chris+an") == "Christian"
    assert canonical_fuzzy("Galla+n") == "Gallatin"
    assert canonical_fuzzy("Faye e") == "Fayette"
    assert canonical_fuzzy("De Wi") == "DeWitt"
    assert canonical_fuzzy("Pia") == "Piatt"
    assert canonical_fuzzy("Sco") == "Scott"
    assert canonical_fuzzy("Eﬃngham") == "Effingham"
    assert canonical_fuzzy("Jeﬀerson") == "Jefferson"


def test_ambiguous_scraps_are_left_unmatched():
    from tracker.counties import canonical_fuzzy
    assert canonical_fuzzy("Ma") is None       # Macon/Madison/Marion/...
    assert canonical_fuzzy("C") is None
    assert canonical_fuzzy("Aetna Better Health") is None


def test_exact_names_still_win():
    from tracker.counties import canonical_fuzzy
    assert canonical_fuzzy("Clay") == "Clay"
    assert canonical_fuzzy("Clark") == "Clark"


def test_prefix_wins_over_subsequence():
    """'Pia' is a subsequence of Peoria too; ligature loss preserves prefixes."""
    from tracker.counties import canonical_fuzzy
    assert canonical_fuzzy("Pia") == "Piatt"


def test_display_names_are_human_readable():
    from tracker.counties import display
    assert display("StClair") == "St. Clair"
    assert display("RockIsland") == "Rock Island"
    assert display("DeWitt") == "De Witt"
    assert display("Cook") == "Cook"
