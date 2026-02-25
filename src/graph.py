from __future__ import annotations

from src.state import StateGraph


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

builder.add_edge("start", "RepoInvestigator", parallel=True)
builder.add_edge("start", "DocAnalyst", parallel=True)
builder.add_edge("start", "VisionInspector", parallel=True)


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
# builder.add_edge("EvidenceAggregator", "ProsecutorJudge", parallel=True)
# builder.add_edge("EvidenceAggregator", "DefenseJudge", parallel=True)
# builder.add_edge("EvidenceAggregator", "TechLeadJudge", parallel=True)
# builder.add_edge("ProsecutorJudge", "ChiefJustice")
# builder.add_edge("DefenseJudge", "ChiefJustice")
# builder.add_edge("TechLeadJudge", "ChiefJustice")


# Explicitly set the start and end nodes for the graph.
builder.set_start("start")
builder.set_end("EvidenceAggregator")


# Expose the configured graph builder for use elsewhere in the application.
audit_graph = builder

