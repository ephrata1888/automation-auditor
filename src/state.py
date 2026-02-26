import operator
from typing import Annotated, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


# Re-export LangGraph builder for graph wiring.
from langgraph.graph import StateGraph as _StateGraph


def StateGraph(name: str = "audit_graph"):
    """Create a StateGraph configured for Automation Auditor with AgentState schema."""
    return _StateGraph(AgentState)


# --- Detective Output ---


class Evidence(BaseModel):
    goal: str = Field()
    found: bool = Field(description="Whether the artifact exists")
    content: Optional[str] = Field(default=None)
    location: str = Field(
        description="File path or commit hash",
    )
    rationale: str = Field(
        description="Your rationale for your confidence "
        "on the evidence you find for this particular goal",
    )
    confidence: float


# --- Judge Output ---


class JudicialOpinion(BaseModel):
    """Structured output from judge nodes: score, argument, and cited evidence."""

    judge: Literal["Prosecutor", "Defense", "TechLead"]
    criterion_id: str = Field(default="default")
    score: int = Field(ge=1, le=5, description="1-5 scale")
    argument: str = Field(description="Judge's argument for the score")
    cited_evidence: List[str] = Field(
        default_factory=list,
        description="Cited evidence IDs or locations",
    )


# --- Chief Justice Output ---


class CriterionResult(BaseModel):
    dimension_id: str
    dimension_name: str
    final_score: int = Field(ge=1, le=5)
    judge_opinions: List[JudicialOpinion]
    dissent_summary: Optional[str] = Field(
        default=None,
        description="Required when score variance > 2",
    )
    remediation: str = Field(
        description="Specific file-level instructions "
        "for improvement",
    )


class AuditReport(BaseModel):
    repo_url: str
    executive_summary: str
    overall_score: float
    criteria: List[CriterionResult]
    remediation_plan: str


# --- Graph State ---


class _AgentStateRequired(TypedDict):
    """Required state keys for graph input."""

    repo_url: str
    pdf_path: str
    rubric_dimensions: List[Dict]


class _AgentStateOptional(TypedDict, total=False):
    """Optional state keys (reducers + error flags for conditional routing)."""

    evidences: Annotated[
        Dict[str, List[Evidence]],
        operator.ior,
    ]
    opinions: Annotated[
        List[JudicialOpinion],
        operator.add,
    ]
    final_report: Optional[AuditReport]
    report_md: Optional[str]
    repo_error: bool
    doc_error: bool
    vision_error: bool
    judge_parse_error: Optional[str]
    needs_reeval: bool


class AgentState(_AgentStateRequired, _AgentStateOptional):
    """Graph state with reducers for parallel execution (prevent overwrites)."""