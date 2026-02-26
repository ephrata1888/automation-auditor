"""
EvidenceAggregator node: fan-in collection of Evidence objects.

Collects Evidence from all detective nodes (RepoInvestigator, DocAnalyst,
VisionInspector) and normalizes/merges them. The state reducer (operator.ior)
automatically merges dict updates from parallel detectives; this node performs
additional normalization and serves as the synchronization point before judges.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from src.state import Evidence

logger = logging.getLogger(__name__)


def evidence_aggregator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    EvidenceAggregator node: combines and normalizes collected evidence.

    Detective nodes return {"evidences": {"RepoInvestigator": [...], ...}} and
    the state reducer (operator.ior) merges these. This node optionally
    validates/normalizes the merged evidence and passes through to the next
    superstep. It serves as the fan-in synchronization point before judges.
    """
    evidences: Dict[str, List[Evidence]] = dict(state.get("evidences") or {})
    # Pass through; reducer already merged. Could add validation/normalization here.
    return {}
