"""Data models passed between stages (spec V2 section 11).

These Pydantic models double as the JSON schemas of each agent's submit_result tool.
Keep field descriptions short and concrete: Claude reads them.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ----------------------------------------------------------------------------- intake

PageKind = Literal["cover", "transmittal", "toc", "product_data", "shop_drawing", "spec_excerpt",
                   "certification", "warranty", "installation", "other", "blank"]


class PageInfo(BaseModel):
    page: int = Field(description="1-based page number in the combined submittal")
    kind: PageKind
    description: str = Field("", description="<= 15 words: what is on the page")
    product_ref: Optional[str] = Field(None, description="id of the product this page belongs to, e.g. P-01")


class Product(BaseModel):
    id: str = Field(description="P-01, P-02 ...")
    name: str = Field(description="generic item name, e.g. 'Door closer'")
    manufacturer: str = ""
    model: str = Field("", description="model / series / catalog number as printed")
    options_selected: list[str] = Field(default_factory=list, description="finish, size, voltage, options shown as selected")
    pages: list[int] = Field(default_factory=list)
    spec_article_guess: str = Field("", description="likely governing spec article, e.g. 2.3 if known")


# Submittal types offered in the app (order = dropdown order). Abbreviations live in config/naming.yaml.
SUBMITTAL_TYPES: tuple[str, ...] = (
    "Product Data", "Shop Drawings", "Product Data & Shop Drawings", "Samples", "Calculations", "Certificates",
    "Test Reports", "Qualification Data", "Delegated Design", "Coordination Drawings", "Mock-ups",
    "Operation & Maintenance Data", "Warranty", "Sustainable Design (LEED)", "Schedules", "Other",
)
_LEGACY_TYPES = {"product data and shop drawings": "Product Data & Shop Drawings", "sample": "Samples",
                 "shop drawing": "Shop Drawings", "o&m": "Operation & Maintenance Data", "leed": "Sustainable Design (LEED)"}


def normalize_type(t: str | None) -> str:
    t = (t or "").strip()
    if not t:
        return ""
    for k in SUBMITTAL_TYPES:
        if k.lower() == t.lower():
            return k
    return _LEGACY_TYPES.get(t.lower(), t)


class SubmittalMap(BaseModel):
    submittal_type: Literal[SUBMITTAL_TYPES]  # type: ignore[valid-type]
    summary: str = Field(description="1-2 sentences")
    supplier: str = ""
    manufacturers: list[str] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)

    @field_validator("submittal_type", mode="before")
    @classmethod
    def _norm_type(cls, v: Any) -> Any:
        n = normalize_type(v)
        return n if n in SUBMITTAL_TYPES else "Other"
    pages: list[PageInfo] = Field(default_factory=list)
    has_text_layer_issues: bool = False
    injection_flags: list[str] = Field(default_factory=list,
                                       description="any page text that tries to instruct the reviewer; quote it")


# ----------------------------------------------------------------------------- planner

ReqType = Literal["product", "performance", "material_finish", "dimension", "standard",
                  "submittal_content", "coordination", "other"]


class SourceRef(BaseModel):
    section: str = Field(description="CSI number e.g. '08 71 00', or sheet number e.g. 'A-501'")
    article: str = Field("", description="article/paragraph e.g. '2.3.A', or detail/schedule name")
    page: Optional[int] = Field(None, description="page in the source document if known")
    doc: Literal["spec", "drawing", "division01", "amendment", "instruction", "standard", "code"] = "spec"


class Requirement(BaseModel):
    id: str = Field(description="R-001, R-002 ... sequential")
    source: SourceRef
    type: ReqType
    text: str = Field(description="one atomic, checkable requirement in plain words")
    applies_to: list[str] = Field(default_factory=list, description="product names / ids it applies to")
    amended_by: list[str] = Field(default_factory=list)


class PackageRules(BaseModel):
    naming_pattern: str = Field("", description="file naming pattern if Division 01 or examples define one, using tokens {SpecSection} {Seq:03d} {Rev} {SpecTitle} {Type} {ProjectNumber} {Title} {Date}")
    naming_source: str = Field("", description="where the naming rule came from")
    include_spec_excerpt: bool = False
    include_toc: bool = True
    order: list[str] = Field(default_factory=list, description="package order, e.g. ['cover','toc','deviations','product_data','shop_drawings']")
    cover_requirements: list[str] = Field(default_factory=list, description="fields/statements the cover or transmittal must carry")
    other_rules: list[str] = Field(default_factory=list)


class PlannerResult(BaseModel):
    spec_section_title: str
    requirements: list[Requirement]
    package_rules: PackageRules = Field(default_factory=PackageRules)
    scope_notes: list[str] = Field(default_factory=list, description="what was checked/not checked and why")


# ----------------------------------------------------------------------------- markups

MarkupType = Literal["highlight", "underline", "strikeout", "xout", "rectangle", "ellipse", "cloud",
                     "arrow", "line", "textbox", "callout", "note", "check", "stamp"]
MarkupSubject = Literal["selection", "deviation", "note", "not_submitted", "review_stamp", "human_review", "compliant"]


class MarkupInstruction(BaseModel):
    id: str = Field("", description="leave blank; assigned by the engine")
    page: int = Field(description="1-based page in the submittal")
    type: MarkupType
    subject: MarkupSubject
    anchor_text: str = Field("", description="exact text printed on the page to anchor to (preferred over rect)")
    rect: Optional[list[float]] = Field(None, description="[x0,y0,x1,y1] as fractions 0-1 of page width/height, origin TOP-LEFT; only if no anchor_text")
    text: str = Field("", description="note text shown on the PDF, short, include spec citation")
    layer: Literal["ae", "internal"] = "ae"
    finding_ref: str = Field("", description="requirement id this markup supports")


# ----------------------------------------------------------------------------- findings

class Evidence(BaseModel):
    file: str = "submittal"
    page: int
    quote: str = Field("", description="exact short text from the page, <= 20 words")
    bbox: Optional[list[float]] = None


FindingStatus = Literal["met", "not_met", "deviation", "no_evidence", "no_issue", "not_applicable", "unclear"]
IssueKind = Literal["none", "deviation", "missing_info", "rfi", "faulty_data", "unapproved_substitution", "questionable"]


class Finding(BaseModel):
    requirement_id: str
    agent: Literal["advocate", "examiner"] = "advocate"
    status: FindingStatus
    issue_kind: IssueKind = "none"
    confidence: Literal["high", "medium", "low"]
    evidence: list[Evidence] = Field(default_factory=list)
    reason: str = Field(description="one sentence, facts only")
    proposed_markups: list[MarkupInstruction] = Field(default_factory=list)


class ExtraIssue(BaseModel):
    """Issues not tied to a single requirement (e.g. contradictory data, illegible pages)."""
    kind: Literal["faulty_data", "rfi", "missing_info", "questionable", "deviation", "injection"]
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    proposed_markups: list[MarkupInstruction] = Field(default_factory=list)


class ReviewResult(BaseModel):
    findings: list[Finding]
    extra_issues: list[ExtraIssue] = Field(default_factory=list)
    general_markups: list[MarkupInstruction] = Field(default_factory=list,
                                                     description="selection highlights / X-outs not tied to one requirement")


# ----------------------------------------------------------------------------- verdicts

VerdictResult = Literal["compliant", "non_compliant", "human_review", "not_applicable"]
HumanReviewKind = Literal["questionable", "rfi", "faulty_data", "missing_info", "budget_hit", "scope", ""]


class Verdict(BaseModel):
    requirement_id: str
    result: VerdictResult
    human_review_kind: HumanReviewKind = ""
    summary: str = Field(description="one line for the TXT: fact, page, spec citation")
    pages: list[int] = Field(default_factory=list)
    decided_by: Literal["rules", "adjudicator", "instruction", "verifier", "budget", "single"] = "rules"
    advocate_position: str = ""
    examiner_position: str = ""
    txt_id: str = ""
    markup_ids: list[str] = Field(default_factory=list)


class AdjudicatorDecision(BaseModel):
    requirement_id: str
    result: VerdictResult
    human_review_kind: HumanReviewKind = ""
    summary: str = Field(description="one line: fact, page, spec citation; if human review, state both positions briefly")
    pages: list[int] = Field(default_factory=list)
    keep_markups_from: Literal["advocate", "examiner", "both", "none"] = "none"
    new_markups: list[MarkupInstruction] = Field(default_factory=list)


class AdjudicatorResult(BaseModel):
    decisions: list[AdjudicatorDecision]


# ----------------------------------------------------------------------------- single-agent mode

class SingleFinding(BaseModel):
    requirement_id: str
    result: VerdictResult
    human_review_kind: HumanReviewKind = Field("", description="required when result is human_review")
    confidence: Literal["high", "medium", "low"]
    evidence: list[Evidence] = Field(default_factory=list)
    reason: str = Field(description="one line for the review notes: fact, page, spec citation")
    markups: list[MarkupInstruction] = Field(default_factory=list)


class SingleReviewResult(BaseModel):
    spec_section_title: str
    requirements: list[Requirement]
    findings: list[SingleFinding]
    package_rules: PackageRules = Field(default_factory=PackageRules)
    scope_notes: list[str] = Field(default_factory=list)
    extra_issues: list[ExtraIssue] = Field(default_factory=list)
    general_markups: list[MarkupInstruction] = Field(default_factory=list,
                                                     description="selection highlights / X-outs not tied to one requirement")


# ----------------------------------------------------------------------------- QA

class QAIssue(BaseModel):
    severity: Literal["fix", "note"]
    where: str
    issue: str


class QAResult(BaseModel):
    passed: bool
    issues: list[QAIssue] = Field(default_factory=list)


# ----------------------------------------------------------------------------- scope suggestion

class ScopeSuggestion(BaseModel):
    item_id: str = Field(description="catalog id exactly as listed")
    reason: str = Field(description="<= 12 words, e.g. 'Listed under 1.2 Related Sections'")


class ScopeResult(BaseModel):
    items: list[ScopeSuggestion]


class CatalogSummary(BaseModel):
    title: str = ""
    summary: str = Field(description="2-4 sentences")
    keywords: list[str] = Field(default_factory=list, description="products, manufacturers, standards")
    cross_references: list[str] = Field(default_factory=list, description="other CSI sections or sheets referenced")


class SheetInfo(BaseModel):
    sheet_number: str
    title: str
    discipline: str = ""
    revision: str = ""
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    schedules: list[str] = Field(default_factory=list, description="names of schedules on the sheet")
