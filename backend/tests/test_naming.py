from asm.outputs import naming


def test_default_pattern():
    params = {"spec_section": "08 71 00", "submittal_no": "3", "revision": "0", "spec_title": "Door Hardware",
              "submittal_type": "Product Data"}
    pat, src = naming.choose_pattern(params)
    assert naming.render(pat, naming.tokens(params, {})) == "08 71 00-003-0 Door Hardware - Product Data"
    assert src == "company default"


def test_infer_from_example():
    pat = naming.infer_pattern_from_example("Example - 08 11 13-001-0 Hollow Metal Doors - Product Data.pdf")
    assert pat == "{SpecSection}-{Seq:03d}-{Rev} {SpecTitle} - {Type}"
    pat2 = naming.infer_pattern_from_example("087100_0004_A Door Hardware - Shop Drawings.pdf")
    params = {"spec_section": "26 05 19", "submittal_no": "12", "revision": "B", "spec_title": "Cables", "submittal_type": "Shop Drawings"}
    assert naming.render(pat2, naming.tokens(params, {})) == "260519_0012_B Cables - Shop Drawings"


def test_invalid_chars_and_length():
    params = {"spec_section": "08 71 00", "submittal_no": "1", "revision": "0", "spec_title": 'Door: "Hardware"/Sets?' * 20,
              "submittal_type": "Product Data"}
    out = naming.render("{SpecSection}-{Seq:03d}-{Rev} {SpecTitle} - {Type}", naming.tokens(params, {}))
    assert len(out) <= 150 and not any(c in out for c in '<>:"/\\|?*')


def test_parse_seq():
    assert naming.parse_seq("08 71 00-004", "08 71 00") == (4, "004")
    assert naming.parse_seq("", "") == (1, "001")
