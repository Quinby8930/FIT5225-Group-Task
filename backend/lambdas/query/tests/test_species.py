"""Tests for the scientific -> team tag name mapper (the tag-name contract)."""

from app.species import SpeciesMapper, get_mapper


def _mapper():
    return get_mapper()


def test_wombat_short_name():
    assert _mapper().common_name("Vombatus_ursinus") == "wombat"


def test_magpie_short_name():
    assert _mapper().common_name("Gymnorhina_tibicen") == "magpie"


def test_both_dingo_classes_map_to_dingo():
    assert _mapper().common_name("Canis_familiaris") == "dingo"
    assert _mapper().common_name("Canis_dingo") == "dingo"


def test_case_insensitive():
    assert _mapper().common_name("Homo_sapiens") == "human"
    assert _mapper().common_name("homo_sapiens") == "human"
    assert _mapper().common_name("Casuarius_casuarius") == "cassowary"


def test_cat_mouse_and_rat_labels_do_not_collide():
    mapper = _mapper()
    assert mapper.common_name("Felis_catus") == "cat"
    assert mapper.common_name("Mus_musculus") == "mouse"
    assert mapper.common_name("Rattus") == "rat"
    assert mapper.common_name("Rattus_fuscipes") == "rat"
    assert mapper.common_name("Rattus_rattus") == "rat"


def test_unknown_class_passes_through():
    assert _mapper().common_name("Madeup_species") == "Madeup_species"
