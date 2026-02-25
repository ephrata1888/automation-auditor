from __future__ import annotations

from src.state import StateGraph
from src.state import AgentState


"""
Automation Auditor StateGraph wiring.

This module defines the high-level orchestration graph used by the Automation
Auditor system. The graph is intentionally simple and explicit so that static
analysis tools (e.g., RepoInvestigator) can inspect the topology using the
Python AST.
"""


# Instantiate the StateGraph builder for this audit.
builder = StateGraph(name="audit_graph")


# ---------------------------------------------------------------------------
# Fan-out: Detectives
# ---------------------------------------------------------------------------
#
# All three detective nodes branch out in parallel from a common "start" node.
# Each detective inspects a different modality of the audit target:
# - RepoInvestigator: repository and git history
# - DocAnalyst: PDF report and documentation
# - VisionInspector: diagrams and images within the report

def repo_condition_router(state: AgentState) -> str:
    if state.repo_error:
        return "error"
    return "success"
def doc_condition_router(state: AgentState) -> str:
    if state.doc_error:
        return "error"
    return "success"
def vision_condition_router(state: AgentState) -> str:
    if state.vision_error:
        return "error"
    return "success"


builder.add_conditional_edges(
    "RepoInvestigator",
    repo_condition_router,
    {
        "success": "EvidenceAggregator",
        "error": "ErrorHandler"
    }
)
builder.add_conditional_edges(
    "DocAnalyst",
    doc_condition_router,
    {
        "success": "EvidenceAggregator",
        "error": "ErrorHandler"
    }
)
builder.add_conditional_edges(
    "VisionInspector",
    vision_condition_router,
    {
        "success": "EvidenceAggregator",
        "error": "ErrorHandler"
    }
)


# ---------------------------------------------------------------------------
# Fan-in: EvidenceAggregator
# ---------------------------------------------------------------------------
#
# All detective paths converge into a single EvidenceAggregator node which is
# responsible for combining and normalizing the collected evidence.

builder.add_edge("RepoInvestigator", "EvidenceAggregator")
builder.add_edge("DocAnalyst", "EvidenceAggregator")
builder.add_edge("VisionInspector", "EvidenceAggregator")


# ---------------------------------------------------------------------------
# Placeholder: Judges and Chief Justice
# ---------------------------------------------------------------------------
#
# Future work:
# - Add Prosecutor, Defense, and TechLead judge nodes that operate in parallel
#   on the aggregated evidence.
# - Add a ChiefJustice or Synthesis node that consumes all judge opinions and
#   produces the final audit report.
#
# Example (to be implemented later):
builder.add_edge("EvidenceAggregator", "Prosecutor", parallel=True)
builder.add_edge("EvidenceAggregator", "Defense", parallel=True)
builder.add_edge("EvidenceAggregator", "TechLead", parallel=True)
builder.add_edge("Prosecutor", "ChiefJustice")
builder.add_edge("Defense", "ChiefJustice")
builder.add_edge("TechLead", "ChiefJustice")


# Explicitly set the start and end nodes for the graph.
builder.set_start("start")
builder.set_end("EvidenceAggregator")


# Expose the configured graph builder for use elsewhere in the application.
audit_graph = builder

