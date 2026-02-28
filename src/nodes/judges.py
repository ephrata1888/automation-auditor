"""
Judge nodes: Prosecutor, Defense, TechLead.

Each judge evaluates the same Evidence in parallel and produces a JudicialOpinion
(score, argument, cited_evidence). Includes error handling for parsing failures:
invalid output routes to retry or partial scoring.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from src.state import Evidence, JudicialOpinion

logger = logging.getLogger(__name__)

# Retry and rate-limit settings for free-tier API stability.
MAX_LLM_RETRIES = 3
INITIAL_RETRY_DELAY_SEC = 1.0
RETRY_BACKOFF_MULTIPLIER = 2.0
DELAY_BETWEEN_CALLS_SEC = 0.75
MAX_EVIDENCE_SUMMARY_CHARS = 8000

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


def _partial_opinion(judge_name: str, error_msg: str, criterion_id: str = "default") -> JudicialOpinion:
    """Return a fallback opinion when parsing fails (partial scoring)."""
    return JudicialOpinion(
        judge=judge_name,
        criterion_id=criterion_id,
        score=3,
        argument=f"Parse failure: {error_msg}. Assigning neutral score 3.",
        cited_evidence=[],
    )


# ---------------------------------------------------------------------------
# Prompt and retry helpers
# ---------------------------------------------------------------------------


def _truncate_evidence_summary(text: str, max_chars: int = MAX_EVIDENCE_SUMMARY_CHARS) -> str:
    """Truncate evidence summary to avoid token limits on free-tier APIs."""
    if len(text) <= max_chars:
        return text
    truncated = text[: max_chars - 80] + "\n\n[... evidence truncated for length ...]"
    logger.debug("Truncated evidence summary from %d to %d chars", len(text), len(truncated))
    return truncated


def _evaluate_evidence(
    evidence_summary: str,
    judge_role: str,
    dimension: Dict[str, Any],
) -> str | Dict[str, Any]:
    """
    Invoke LLM to evaluate evidence for a given judge role and rubric dimension.

    Uses exponential retry, truncates long evidence, and logs full exceptions.
    Returns a dict with score, argument, cited_evidence, criterion_id.
    Falls back to neutral opinion only after all retries fail.
    """
    criterion_id = dimension.get("id", "default")
    dimension_name = dimension.get("name", criterion_id)
    forensic_instruction = dimension.get("forensic_instruction", "")

    try:
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import]
        from src.llm import get_chat_llm
    except Exception as exc:  # pragma: no cover - import environment specific
        logger.warning("LLM client unavailable for %s: %s", judge_role, exc)
        return {
            "score": 3,
            "argument": f"[{judge_role}] Neutral fallback opinion due to missing LLM client.",
            "cited_evidence": [],
            "criterion_id": criterion_id,
        }

    fallback = {
        "score": 3,
        "argument": f"[{judge_role}] Neutral fallback opinion due to LLM error.",
        "cited_evidence": [],
        "criterion_id": criterion_id,
    }

    summary_truncated = _truncate_evidence_summary(evidence_summary)

    system_prompt = (
        "You are the '{judge_role}' judge in an automated code audit.\n"
        "You receive a summarized set of evidences about a repository and report.\n"
        "Your task is to evaluate the evidence against ONE specific rubric dimension.\n"
        "Output a structured object with:\n"
        "  - score: integer from 1 to 5 (no floats)\n"
        "  - argument: string explaining your reasoning for the score\n"
        "  - cited_evidence: list of strings referencing specific evidence items\n"
        "  - criterion_id: MUST be exactly \"{criterion_id}\" (the dimension id)\n"
    ).format(judge_role=judge_role, criterion_id=criterion_id)

    user_prompt = (
        "Judge role: {judge_role}\n\n"
        "Rubric dimension: {dimension_name} (id: {criterion_id})\n"
        "Forensic instruction for this dimension: {forensic}\n\n"
        "Evidence summary:\n{summary}\n"
    ).format(
        judge_role=judge_role,
        dimension_name=dimension_name,
        criterion_id=criterion_id,
        forensic=forensic_instruction or "Evaluate overall alignment with the rubric.",
        summary=summary_truncated,
    )

    last_exception: Exception | None = None
    delay = INITIAL_RETRY_DELAY_SEC

    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            llm = get_chat_llm(judge_role, temperature=0)
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

            return {
                "score": score,
                "argument": argument,
                "cited_evidence": [str(c) for c in cited_evidence],
                "criterion_id": criterion_id,
            }
        except Exception as exc:
            last_exception = exc
            logger.exception(
                "Judge LLM call failed for %s (dimension %s) attempt %d/%d: %s",
                judge_role,
                criterion_id,
                attempt,
                MAX_LLM_RETRIES,
                exc,
            )
            if attempt < MAX_LLM_RETRIES:
                logger.info(
                    "Retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    MAX_LLM_RETRIES,
                )
                time.sleep(delay)
                delay *= RETRY_BACKOFF_MULTIPLIER

    logger.warning(
        "All %d retries exhausted for %s (dimension %s). Last error: %s",
        MAX_LLM_RETRIES,
        judge_role,
        criterion_id,
        last_exception,
    )
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
    rubric_dimensions = state.get("rubric_dimensions") or []
    evidence_summary = _build_evidence_summary(evidences)

    opinions: List[JudicialOpinion] = []
    for i, dimension in enumerate(rubric_dimensions):
        if i > 0:
            time.sleep(DELAY_BETWEEN_CALLS_SEC)
        criterion_id = dimension.get("id", "default")
        raw = _evaluate_evidence(evidence_summary, "Prosecutor", dimension)
        opinion = _parse_opinion(raw, "Prosecutor")
        if opinion is None:
            opinions.append(_partial_opinion("Prosecutor", "invalid structured output", criterion_id))
        else:
            opinions.append(opinion)

    return {"opinions": opinions}


@traceable(name="Defense")  # ensures a distinct trace entry per judge node
def defense_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Defense node: generous evaluation rewarding effort and intent.
    Focuses on creative workarounds and positive intent.
    """
    evidences = state.get("evidences") or {}
    rubric_dimensions = state.get("rubric_dimensions") or []
    evidence_summary = _build_evidence_summary(evidences)

    opinions: List[JudicialOpinion] = []
    for i, dimension in enumerate(rubric_dimensions):
        if i > 0:
            time.sleep(DELAY_BETWEEN_CALLS_SEC)
        criterion_id = dimension.get("id", "default")
        raw = _evaluate_evidence(evidence_summary, "Defense", dimension)
        opinion = _parse_opinion(raw, "Defense")
        if opinion is None:
            opinions.append(_partial_opinion("Defense", "invalid structured output", criterion_id))
        else:
            opinions.append(opinion)

    return {"opinions": opinions}


@traceable(name="TechLead")  # ensures a distinct trace entry per judge node
def tech_lead_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    TechLead node: architectural and maintainability evaluation.
    Highest weight in functionality/architecture criteria.
    """
    evidences = state.get("evidences") or {}
    rubric_dimensions = state.get("rubric_dimensions") or []
    evidence_summary = _build_evidence_summary(evidences)

    opinions: List[JudicialOpinion] = []
    for i, dimension in enumerate(rubric_dimensions):
        if i > 0:
            time.sleep(DELAY_BETWEEN_CALLS_SEC)
        criterion_id = dimension.get("id", "default")
        raw = _evaluate_evidence(evidence_summary, "TechLead", dimension)
        opinion = _parse_opinion(raw, "TechLead")
        if opinion is None:
            opinions.append(_partial_opinion("TechLead", "invalid structured output", criterion_id))
        else:
            opinions.append(opinion)

    return {"opinions": opinions}
