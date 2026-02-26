"""
Automation Auditor StateGraph wiring.

This module defines the high-level orchestration graph used by the Automation
Auditor system. The graph is intentionally simple and explicit so that static
analysis tools (e.g., RepoInvestigator) can inspect the topology using the
Python AST.

Pipeline: START -> [Detectives in parallel] -> EvidenceAggregator
          -> [Judges in parallel] -> ChiefJustice -> END
"""

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import END, START

from src.state import StateGraph
from src.nodes.aggregator import evidence_aggregator_node
from src.nodes.detectives import (
    doc_analyst_node,
    error_handler_node,
    repo_investigator_node,
    vision_inspector_node,
)
from src.nodes.judges import defense_node, prosecutor_node, tech_lead_node
from src.nodes.justice import chief_justice_node


# Instantiate the StateGraph builder for this audit.
builder = StateGraph(name="audit_graph")


# ---------------------------------------------------------------------------
# Add all nodes
# ---------------------------------------------------------------------------

builder.add_node("RepoInvestigator", repo_investigator_node)
builder.add_node("DocAnalyst", doc_analyst_node)
builder.add_node("VisionInspector", vision_inspector_node)
builder.add_node("EvidenceAggregator", evidence_aggregator_node)
builder.add_node("ErrorHandler", error_handler_node)
builder.add_node("Prosecutor", prosecutor_node)
builder.add_node("Defense", defense_node)
builder.add_node("TechLead", tech_lead_node)
builder.add_node("ChiefJustice", chief_justice_node)


# ---------------------------------------------------------------------------
# Fan-out: Detectives (from START)
# ---------------------------------------------------------------------------
#
# All three detective nodes branch out in parallel from START.
# Each detective inspects a different modality of the audit target:
# - RepoInvestigator: repository and git history
# - DocAnalyst: PDF report and documentation
# - VisionInspector: diagrams and images within the report

builder.add_edge(START, "RepoInvestigator")
builder.add_edge(START, "DocAnalyst")
builder.add_edge(START, "VisionInspector")


# ---------------------------------------------------------------------------
# Conditional routing: Detectives -> EvidenceAggregator | ErrorHandler
# ---------------------------------------------------------------------------

def _repo_condition_router(state: Dict[str, Any]) -> str:
    if state.get("repo_error"):
        return "error"
    return "success"


def _doc_condition_router(state: Dict[str, Any]) -> str:
    if state.get("doc_error"):
        return "error"
    return "success"


def _vision_condition_router(state: Dict[str, Any]) -> str:
    if state.get("vision_error"):
        return "error"
    return "success"


builder.add_conditional_edges(
    "RepoInvestigator",
    _repo_condition_router,
    {"success": "EvidenceAggregator", "error": "ErrorHandler"},
)
builder.add_conditional_edges(
    "DocAnalyst",
    _doc_condition_router,
    {"success": "EvidenceAggregator", "error": "ErrorHandler"},
)
builder.add_conditional_edges(
    "VisionInspector",
    _vision_condition_router,
    {"success": "EvidenceAggregator", "error": "ErrorHandler"},
)

# ErrorHandler continues to EvidenceAggregator so the pipeline can proceed
builder.add_edge("ErrorHandler", "EvidenceAggregator")


# ---------------------------------------------------------------------------
# Fan-in: EvidenceAggregator (receives from Detectives + ErrorHandler)
# ---------------------------------------------------------------------------
#
# All detective paths converge into EvidenceAggregator which merges evidence.
# The state reducer (operator.ior) handles dict merging; this node syncs.

# No additional edges needed; conditional edges above route into EvidenceAggregator.


# ---------------------------------------------------------------------------
# Fan-out: Judges (from EvidenceAggregator)
# ---------------------------------------------------------------------------
#
# Prosecutor, Defense, TechLead evaluate the same Evidence in parallel.

builder.add_edge("EvidenceAggregator", "Prosecutor")
builder.add_edge("EvidenceAggregator", "Defense")
builder.add_edge("EvidenceAggregator", "TechLead")


# ---------------------------------------------------------------------------
# Judge conditional routing: parse failure -> ChiefJustice (partial scoring)
# ---------------------------------------------------------------------------
#
# Judges handle parse failures internally (partial scoring). Always route to
# ChiefJustice. Optional: add retry path for judge_parse_error.

def _prosecutor_router(state: Dict[str, Any]) -> str:
    # On parse failure we use partial scoring; always continue to ChiefJustice
    return "success"


def _defense_router(state: Dict[str, Any]) -> str:
    return "success"


def _tech_lead_router(state: Dict[str, Any]) -> str:
    return "success"


builder.add_conditional_edges(
    "Prosecutor",
    _prosecutor_router,
    {"success": "ChiefJustice"},
)
builder.add_conditional_edges(
    "Defense",
    _defense_router,
    {"success": "ChiefJustice"},
)
builder.add_conditional_edges(
    "TechLead",
    _tech_lead_router,
    {"success": "ChiefJustice"},
)


# ---------------------------------------------------------------------------
# Fan-in: ChiefJustice (receives from all three judges)
# ---------------------------------------------------------------------------
#
# ChiefJustice collects JudicialOpinions, applies deterministic rules, and
# produces the final Markdown report.

builder.add_edge("ChiefJustice", END)


# Expose the configured graph builder for use elsewhere in the application.
audit_graph = builder
