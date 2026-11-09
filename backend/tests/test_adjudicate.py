from asm.agents.adjudicate import quote_on_page, rule_verdicts, verify_findings
from asm.schemas import Evidence, Finding, PlannerResult, Requirement, ReviewResult, SourceRef


def req(i):
    return Requirement(id=f"R-00{i}", source=SourceRef(section="08 71 00", article=f"2.{i}.A"), type="standard", text="x")


def F(rid, agent, status, conf="high", kind="none", page=1, quote="Grade 1"):
    return Finding(requirement_id=rid, agent=agent, status=status, confidence=conf, issue_kind=kind,
                   evidence=[Evidence(page=page, quote=quote)] if page else [], reason=f"{agent} {status}")


def test_table():
    plan = PlannerResult(spec_section_title="t", requirements=[req(i) for i in range(1, 7)])
    adv = ReviewResult(findings=[F("R-001", "advocate", "met"), F("R-002", "advocate", "no_evidence", "low", page=0),
                                 F("R-003", "advocate", "met"), F("R-004", "advocate", "met", "low"),
                                 F("R-005", "advocate", "met"), F("R-006", "advocate", "not_applicable", page=0)])
    exa = ReviewResult(findings=[F("R-001", "examiner", "no_issue"), F("R-002", "examiner", "deviation", kind="deviation"),
                                 F("R-003", "examiner", "deviation", kind="deviation"), F("R-004", "examiner", "no_issue"),
                                 F("R-005", "examiner", "unclear", kind="rfi"), F("R-006", "examiner", "not_applicable", page=0)])
    v, disputes = rule_verdicts(plan, adv, exa)
    res = {x.requirement_id: (x.result, x.human_review_kind) for x in v}
    assert res["R-001"] == ("compliant", "")
    assert res["R-002"] == ("non_compliant", "")
    assert res["R-004"] == ("human_review", "questionable")
    assert res["R-005"] == ("human_review", "rfi")
    assert res["R-006"] == ("not_applicable", "")
    assert [d["requirement_id"] for d in disputes] == ["R-003"]


def test_verifier_downgrades_bad_citations():
    r = ReviewResult(findings=[F("R-001", "advocate", "met", page=9), F("R-002", "advocate", "met", quote="Grade 7 widget")])
    notes = verify_findings(r, ["Closer Grade 1 cast iron body " * 3])
    assert all(f.confidence == "low" for f in r.findings) and len(notes) >= 2
    assert quote_on_page("grade 1  cast", "Closer Grade 1 cast iron")
