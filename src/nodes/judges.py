"""
Judge nodes: Prosecutor, Defense, TechLead.

Each judge evaluates the same Evidence in parallel and produces a JudicialOpinion
(score, argument, cited_evidence). Includes error handling for parsing failures:
invalid output routes to retry or partial scoring.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from src.state import Evidence, JudicialOpinion

logger = logging.getLogger(__name__)

# Optional LangSmith tracing for node-level observability.
try:  # pragma: no cover - environment specific
    from langsmith import traceable  # type: ignore[import]
except Exception:  # pragma: no cover - environment specific
    def traceable(*_args: Any, **_kwargs: Any):  # type: ignore[no-redef]
        def _decorator(func):
            return func

        return _decorator


class _JudgeLLMOutput(BaseModel):
    """Structured judge output (excluding the fixed judge role)."""

    score: int = Field(default=3, ge=1, le=5)
    argument: str = Field(default="")
    cited_evidence: List[str] = Field(default_factory=list)
    criterion_id: str = Field(default="default")


# ---------------------------------------------------------------------------
# JudicialOpinion parsing and error handling
# ---------------------------------------------------------------------------


def _parse_opinion(raw: str | Dict[str, Any], judge_name: str) -> JudicialOpinion | None:
    """Parse LLM output into JudicialOpinion. Returns None on failure."""
    try:
        if isinstance(raw, str):
            data = json.loads(raw)
        else:
            data = raw
        # Map common LLM field names to our schema
        score = data.get("score", 3)

        # Preserve intentionally empty values while still allowing fallbacks
        if "argument" in data and data["argument"] is not None:
            argument = data["argument"]
        else:
            argument = data.get("reasoning", "")

        if "cited_evidence" in data and data["cited_evidence"] is not None:
            cited_evidence = data["cited_evidence"]
        else:
            cited_evidence = data.get("citations", [])
        criterion_id = data.get("criterion_id", "default")
        return JudicialOpinion(
            judge=judge_name,
            criterion_id=str(criterion_id),
            score=min(5, max(1, int(score))),
            argument=str(argument),
            cited_evidence=[str(c) for c in cited_evidence] if isinstance(cited_evidence, list) else [],
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("Failed to parse %s opinion: %s", judge_name, exc)
        return None


def _partial_opinion(judge_name: str, error_msg: str) -> JudicialOpinion:
    """Return a fallback opinion when parsing fails (partial scoring)."""
    return JudicialOpinion(
        judge=judge_name,
        criterion_id="default",
        score=3,
        argument=f"Parse failure: {error_msg}. Assigning neutral score 3.",
        cited_evidence=[],
    )


# ---------------------------------------------------------------------------
# Judge evaluation 
# ---------------------------------------------------------------------------


def _evaluate_evidence(evidence_summary: str, judge_role: str) -> str | Dict[str, Any]:
    """
    Invoke OpenAI GPT-3.5 (gpt-3.5-turbo) to evaluate evidence for a given judge role.

    The model is instructed to return a JSON object with:
      - score: integer 1–5
      - argument: string reasoning
      - cited_evidence: list of evidence references
      - criterion_id: string

    Uses temperature=0 for deterministic output. On any API or JSON parsing error,
    returns a neutral fallback dict compatible with `_parse_opinion`.
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import]
        from langchain_openai import ChatOpenAI  # type: ignore[import]
    except Exception as exc:  # pragma: no cover - import environment specific
        logger.warning("LangChain OpenAI client unavailable for %s: %s", judge_role, exc)
        return {
            "score": 3,
            "argument": f"[{judge_role}] Neutral fallback opinion due to missing LangChain OpenAI client.",
            "cited_evidence": [],
            "criterion_id": "default",
        }

    fallback = {
        "score": 3,
        "argument": f"[{judge_role}] Neutral fallback opinion due to LLM error.",
        "cited_evidence": [],
        "criterion_id": "default",
    }

    system_prompt = (
        "You are the '{judge_role}' judge in an automated code audit.\n"
        "You receive a summarized set of evidences about a repository and report.\n"
        "Your task is to output a structured object with the following fields:\n"
        "  - score: integer from 1 to 5 (no floats)\n"
        "  - argument: string explaining your reasoning for the score\n"
        "  - cited_evidence: list of strings referencing specific evidence items\n"
        "  - criterion_id: MUST be exactly \"default\" (all judges must use this value)\n"
    ).format(judge_role=judge_role)

    user_prompt = (
        "Judge role: {judge_role}\n\n"
        "Evidence summary:\n"
        "{summary}\n"
    ).format(judge_role=judge_role, summary=evidence_summary)

    try:
        llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
        # gpt-3.5-turbo does not support OpenAI "json_schema" structured outputs;
        # use tool/function calling to keep output structured and traceable.
        structured = llm.with_structured_output(_JudgeLLMOutput, method="function_calling")
        output = structured.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ],
        )

        data = output.model_dump() if hasattr(output, "model_dump") else dict(output)
        score = int(data.get("score", 3))
        argument = str(data.get("argument") or "")
        cited_evidence = data.get("cited_evidence") or []
        if not isinstance(cited_evidence, list):
            cited_evidence = [str(cited_evidence)]

        # Enforce criterion_id="default" so all opinions group together in ChiefJustice.
        return {
            "score": score,
            "argument": argument,
            "cited_evidence": [str(c) for c in cited_evidence],
            "criterion_id": "default",
        }
    except Exception as exc:  # API errors and any other unexpected failures
        logger.warning("Judge LLM call failed for %s: %s", judge_role, exc)
        return fallback


# ---------------------------------------------------------------------------
# Judge node functions (signature: state -> partial state)
# ---------------------------------------------------------------------------


def _build_evidence_summary(evidences: Dict[str, List[Evidence]]) -> str:
    """Build a summary of all evidence for judge prompts."""
    parts: List[str] = []
    for source, items in (evidences or {}).items():
        for e in items:
            parts.append(f"[{source}] {e.goal}: found={e.found}, confidence={e.confidence:.2f}")
            parts.append(f"  rationale: {e.rationale[:200]}...")
    return "\n".join(parts) if parts else "No evidence collected."


@traceable(name="Prosecutor")  # ensures a distinct trace entry per judge node
def prosecutor_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prosecutor node: adversarial evaluation of evidence.
    Focuses on gaps, security flaws, and rigor shortcomings.
    Returns {"opinions": [JudicialOpinion], "judge_parse_error": ...} or partial update.
    """
    evidences = state.get("evidences") or {}
    evidence_summary = _build_evidence_summary(evidences)

    raw = _evaluate_evidence(evidence_summary, "Prosecutor")
    opinion = _parse_opinion(raw, "Prosecutor")

    if opinion is None:
        err_msg = "Prosecutor returned invalid structured output"
        return {
            "opinions": [_partial_opinion("Prosecutor", err_msg)],
            "judge_parse_error": "Prosecutor",
        }
    return {"opinions": [opinion]}


@traceable(name="Defense")  # ensures a distinct trace entry per judge node
def defense_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Defense node: generous evaluation rewarding effort and intent.
    Focuses on creative workarounds and positive intent.
    """
    evidences = state.get("evidences") or {}
    evidence_summary = _build_evidence_summary(evidences)

    raw = _evaluate_evidence(evidence_summary, "Defense")
    opinion = _parse_opinion(raw, "Defense")

    if opinion is None:
        err_msg = "Defense returned invalid structured output"
        return {
            "opinions": [_partial_opinion("Defense", err_msg)],
            "judge_parse_error": "Defense",
        }
    return {"opinions": [opinion]}


@traceable(name="TechLead")  # ensures a distinct trace entry per judge node
def tech_lead_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    TechLead node: architectural and maintainability evaluation.
    Highest weight in functionality/architecture criteria.
    """
    evidences = state.get("evidences") or {}
    evidence_summary = _build_evidence_summary(evidences)

    raw = _evaluate_evidence(evidence_summary, "TechLead")
    opinion = _parse_opinion(raw, "TechLead")

    if opinion is None:
        err_msg = "TechLead returned invalid structured output"
        return {
            "opinions": [_partial_opinion("TechLead", err_msg)],
            "judge_parse_error": "TechLead",
        }
    return {"opinions": [opinion]}
