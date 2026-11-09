from asm.costing import estimate


def test_estimate_reasonable(data):
    e = estimate(40, 12000)
    assert 0.8 < e["mid"] < 4.0, e
    assert e["by_stage"]["advocate"] > e["by_stage"]["planner"]
    assert "qa" not in e["by_stage"]  # LLM QA is off by default
