"""
ChiefJustice node: fan-in synthesis of all judge opinions.

Collects JudicialOpinion objects and applies deterministic rules:
- Security Override: cap score if Prosecutor detects confirmed vulnerabilities
- Fact Supremacy: evidence overrides hallucinated opinions
- Functionality Weighting: TechLead's architectural evaluation carries highest weight
- Variance Re-evaluation: if opinion scores differ by >2, trigger re-evaluation step

Produces a structured Markdown report:
- Executive Summary
- Criterion-by-criterion breakdown
- Dissent explanations
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.state import AuditReport, CriterionResult, Evidence, JudicialOpinion

logger = logging.getLogger(__name__)

try:  # pragma: no cover - environment specific
    from langsmith import traceable  # type: ignore[import]
except Exception:  # pragma: no cover - environment specific
    def traceable(*_args: Any, **_kwargs: Any):  # type: ignore[no-redef]
        def _decorator(func):
            return func

        return _decorator


# ---------------------------------------------------------------------------
# Deterministic synthesis rules
# ---------------------------------------------------------------------------


def _security_override(
    opinions: List[JudicialOpinion],
    evidences: Dict[str, List[Evidence]],
) -> tuple[Optional[int], List[Evidence]]:
    """
    Security Override (Rule of Security).

    If the Prosecutor identifies a confirmed, high-confidence security
    vulnerability, the final score for that criterion must be capped at 3.
    Detection considers:
    - Prosecutor argument text
    - Prosecutor cited_evidence
    - Underlying Evidence objects (goal, rationale, location, content)

    Returns (cap_score, supporting_evidence).
    """
    # Security-relevant patterns for both code and descriptions.
    security_keywords = [
        "unsanitized",
        "unvalidated input",
        "sql injection",
        "command injection",
        "injection",
        "rce",
        "remote code execution",
        "auth bypass",
        "authentication bypass",
        "privilege escalation",
        "security flaw",
        "vulnerability",
        "unsafe deserialization",
        "arbitrary command",
        "arbitrary code",
        "csrf",
        "xss",
        "cross-site scripting",
        "directory traversal",
    ]
    insecure_call_keywords = [
        "os.system",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.run",
        "eval(",
        "exec(",
        "pickle.loads",
    ]

    # Collect Prosecutor opinions for this criterion.
    prosecutor_ops: List[JudicialOpinion] = [op for op in opinions if op.judge == "Prosecutor"]
    if not prosecutor_ops:
        return None, []

    # Index evidences by simple string handles for citation matching.
    all_evidences: List[Evidence] = []
    for items in (evidences or {}).values():
        all_evidences.extend(items)

    def _matches_citation(evidence: Evidence, citation: str) -> bool:
        handle = citation.lower()
        return handle in evidence.location.lower() or handle in evidence.goal.lower()

    def _is_security_evidence(e: Evidence) -> bool:
        text = " ".join(
            [
                e.goal.lower(),
                (e.rationale or "").lower(),
                (e.content or "").lower(),
                e.location.lower(),
            ]
        )
        return any(k in text for k in security_keywords + insecure_call_keywords)

    supporting: List[Evidence] = []

    for op in prosecutor_ops:
        if op.score <= 3:
            continue
        arg_text = op.argument.lower()
        arg_has_security = any(k in arg_text for k in security_keywords + insecure_call_keywords)

        cited_matches: List[Evidence] = []
        for citation in op.cited_evidence:
            for ev in all_evidences:
                if _matches_citation(ev, citation) and _is_security_evidence(ev):
                    cited_matches.append(ev)

        # Also allow uncited but clearly security-focused, high-confidence evidence.
        high_conf_security: List[Evidence] = []
        for ev in all_evidences:
            if not ev.found:
                continue
            if not _is_security_evidence(ev):
                continue
            if ev.confidence < 0.75:
                continue
            high_conf_security.append(ev)

        if arg_has_security and (cited_matches or high_conf_security):
            supporting.extend(cited_matches or high_conf_security)

    # Deduplicate supporting evidences by (goal, location).
    unique_supporting: Dict[tuple[str, str], Evidence] = {}
    for ev in supporting:
        key = (ev.goal, ev.location)
        if key not in unique_supporting:
            unique_supporting[key] = ev

    if not unique_supporting:
        return None, []

    return 3, list(unique_supporting.values())


def _has_documentation_gaps(evidences: Dict[str, List[Evidence]]) -> tuple[bool, List[Evidence]]:
    """
    Rule of Evidence helper.

    Detects whether RepoInvestigator / DocAnalyst evidence indicates missing
    required artifacts (e.g., missing files, shallow/missing PDF concepts).
    Returns (has_gaps, evidences_describing_gaps).
    """
    gap_evidences: List[Evidence] = []
    for source, items in (evidences or {}).items():
        if source not in {"RepoInvestigator", "DocAnalyst"}:
            continue
        for e in items:
            goal_lower = e.goal.lower()
            if not e.found and (
                "file exists:" in goal_lower
                or "concept explained:" in goal_lower
                or "pdf" in e.location.lower()
                or "report" in e.location.lower()
            ):
                gap_evidences.append(e)
    return (len(gap_evidences) > 0, gap_evidences)


def _defense_claims_strong_docs(opinion: JudicialOpinion) -> bool:
    """Heuristic: Defense strongly asserts documentation/metacognition quality."""
    if opinion.judge != "Defense":
        return False
    if opinion.score < 4:
        return False
    text = opinion.argument.lower()
    doc_keywords = [
        "documentation",
        "well-documented",
        "metacognition",
        "pdf report",
        "report",
        "docstring",
        "comments",
    ]
    return any(keyword in text for keyword in doc_keywords)


def _detect_linear_graph(evidences: Dict[str, List[Evidence]]) -> bool:
    """
    Statute of Orchestration helper.

    Uses RepoInvestigator graph evidences to detect a purely linear graph
    (missing fan-out or fan-in). Returns True when evidence strongly suggests
    no parallel fan-out/fan-in is modeled.
    """
    has_fan_out: Optional[bool] = None
    has_fan_in: Optional[bool] = None

    for items in (evidences or {}).values():
        for e in items:
            if e.goal == "Graph models fan-out from nodes":
                has_fan_out = e.found
            elif e.goal == "Graph models fan-in to aggregator nodes":
                has_fan_in = e.found

    # If we have no evidence either way, treat as unknown rather than linear.
    if has_fan_out is None and has_fan_in is None:
        return False

    # Any explicit negative finding (no fan-out or no fan-in) is treated as
    # Orchestration Fraud for rubric purposes.
    if has_fan_out is False or has_fan_in is False:
        return True

    return False


def _state_has_rigor(evidences: Dict[str, List[Evidence]]) -> tuple[bool, List[Evidence]]:
    """
    Statute of Engineering helper for state management rigor.

    Returns (has_rigor, supporting_evidence) based on RepoInvestigator's
    validation of AgentState.
    """
    supporting: List[Evidence] = []
    for items in (evidences or {}).values():
        for e in items:
            if e.goal == "AgentState maintains evidences and judicial opinions":
                supporting.append(e)
    if not supporting:
        # No explicit evidence; treat as unknown but not a violation.
        return True, []
    # Consider rigorous only when at least one evidence object marks it found=True.
    has_rigor = any(e.found for e in supporting)
    return has_rigor, supporting


def _judges_use_structured_output(evidences: Dict[str, List[Evidence]]) -> tuple[bool, List[Evidence]]:
    """
    Structured Output Enforcement helper.

    Returns (uses_structured, evidences) based on RepoInvestigator's check
    for `.with_structured_output(JudicialOpinion)` in judge nodes.
    """
    matches: List[Evidence] = []
    for items in (evidences or {}).values():
        for e in items:
            if e.goal == "Judge LLMs use structured JudicialOpinion output":
                matches.append(e)
    if not matches:
        # No explicit evidence; treat as unknown but not a strict violation.
        return True, []
    uses_structured = any(e.found for e in matches)
    return uses_structured, matches


def _is_security_criterion(criterion_id: str) -> bool:
    """Identify security-related criteria by id heuristics."""
    cid = (criterion_id or "").lower()
    return any(keyword in cid for keyword in ["security", "vulnerability", "auth", "injection"])


def _is_architecture_criterion(criterion_id: str) -> bool:
    """Identify architecture-related criteria by id heuristics."""
    cid = (criterion_id or "").lower()
    return any(keyword in cid for keyword in ["architecture", "design", "modularity", "structure"])


def _techlead_confirms_modular(opinions: List[JudicialOpinion]) -> bool:
    """Heuristic: TechLead confirms modular, workable architecture."""
    for op in opinions:
        if op.judge != "TechLead" or op.score < 4:
            continue
        text = op.argument.lower()
        if any(keyword in text for keyword in ["modular", "modularity", "well-structured", "layered", "clean architecture"]):
            return True
    return False


def _weighted_median(scores: List[int], weights: List[float]) -> int:
    """
    Deterministic weighted median used for high-variance re-evaluation.

    Scores are discrete 1–5 values; we compute the median with respect to
    judge-specific weights.
    """
    if not scores or not weights or len(scores) != len(weights):
        return 3

    items = sorted(zip(scores, weights), key=lambda x: x[0])
    total_weight = sum(max(w, 0.0) for _, w in items)
    if total_weight <= 0:
        return 3

    cumulative = 0.0
    threshold = total_weight / 2.0
    for score, weight in items:
        cumulative += max(weight, 0.0)
        if cumulative >= threshold:
            return int(round(score))
    return int(round(items[-1][0]))


def _weighted_average(opinions: List[JudicialOpinion], judge_weights: Dict[str, float]) -> float:
    """
    Compute a weighted average score using per-judge weights.

    When fewer judges are present, the scores are re-normalized so the final
    value stays in the 1–5 range.
    """
    if not opinions:
        return 3.0
    weighted_sum = 0.0
    weight_sum = 0.0
    for op in opinions:
        w = judge_weights.get(op.judge, 1.0)
        weighted_sum += op.score * w
        weight_sum += w
    if weight_sum <= 0:
        return 3.0
    return weighted_sum / weight_sum


def _score_variance(opinions: List[JudicialOpinion]) -> int:
    """Max difference between any two opinion scores."""
    if len(opinions) < 2:
        return 0
    scores = [op.score for op in opinions]
    return max(scores) - min(scores)


def _compute_final_score(
    opinions: List[JudicialOpinion],
    evidences: Dict[str, List[Evidence]],
    criterion_id: str,
) -> tuple[int, Optional[str], bool]:
    """
    Apply all rules and return (final_score, dissent_summary, needs_reeval).
    """
    if not opinions:
        return 3, None, False

    # Base weights: start equal, then apply functional weighting rules.
    judge_weights: Dict[str, float] = {"Prosecutor": 1.0, "Defense": 1.0, "TechLead": 1.0}

    # Statute flags for dissent explanation.
    technical_debt_override = False
    orchestration_fraud = False
    structured_output_violation = False

    # Rule of Functionality: if TechLead confirms modular architecture for
    # architecture-related criteria, give TechLead highest weight.
    is_arch = _is_architecture_criterion(criterion_id)
    tech_modular = _techlead_confirms_modular(opinions)
    if is_arch and tech_modular:
        judge_weights["TechLead"] = 3.0

    # Rule of Evidence: authoritative hallucination override.
    has_doc_gaps, gap_evidences = _has_documentation_gaps(evidences)
    hallucination_override = False
    defense_overruled = False
    if has_doc_gaps:
        for op in opinions:
            if _defense_claims_strong_docs(op):
                # Make hallucination override authoritative by zeroing Defense weight.
                judge_weights["Defense"] = 0.0
                hallucination_override = True
                defense_overruled = True

    # Start with weighted average using the functional/documentation-aware weights.
    raw = _weighted_average(opinions, judge_weights)

    variance = _score_variance(opinions)
    needs_reeval = variance > 2

    # Variance-triggered re-evaluation strategy.
    scores = [op.score for op in opinions]
    weights_for_median = [judge_weights.get(op.judge, 1.0) for op in opinions]

    prosecutor_score: Optional[int] = next((op.score for op in opinions if op.judge == "Prosecutor"), None)
    techlead_score: Optional[int] = next((op.score for op in opinions if op.judge == "TechLead"), None)

    is_security = _is_security_criterion(criterion_id)

    resolution_clauses: List[str] = []

    if needs_reeval:
        if is_security and prosecutor_score is not None:
            # Prioritize Prosecutor for security-related criteria.
            score = prosecutor_score
            resolution_clauses.append(
                "Conflict resolved by prioritizing the Prosecutor's security-focused score "
                "for this security-related criterion."
            )
        elif is_arch and techlead_score is not None:
            # Prioritize TechLead for architecture-related criteria.
            score = techlead_score
            resolution_clauses.append(
                "Conflict resolved by prioritizing the TechLead's architectural assessment "
                "for this architecture-related criterion."
            )
        else:
            # Otherwise compute a weighted median.
            score = _weighted_median(scores, weights_for_median)
            resolution_clauses.append(
                "Conflict resolved by taking the weighted median of judge scores to balance "
                "disagreement without favoring a single role."
            )
    else:
        score = int(round(raw))

    # Rule of Security: cap scores when Prosecutor finds critical issues.
    security_cap, security_evidence = _security_override(opinions, evidences)
    if security_cap is not None:
        score = min(score, security_cap)
        if security_evidence:
            resolution_clauses.append(
                "Rule of Security applied: confirmed, high-confidence security vulnerabilities "
                "in the referenced files require capping the final score at 3 regardless of "
                "more generous Defense or TechLead opinions."
            )

    # Statute of Orchestration: Orchestration Fraud forces architecture score to 1
    # for the 'graph_orchestration' criterion when a linear graph is detected.
    if criterion_id == "graph_orchestration" and _detect_linear_graph(evidences):
        orchestration_fraud = True
        score = 1

    # Statute of Engineering: Technical Debt for state management rigor when
    # AgentState does not maintain Evidence/JudicialOpinion collections.
    if criterion_id == "state_management_rigor":
        has_rigor, _state_evs = _state_has_rigor(evidences)
        if not has_rigor:
            technical_debt_override = True
            score = 3

    # Structured Output Enforcement / Hallucination Liability: when judges do
    # not enforce structured JudicialOpinion output, cap relevant rubric scores.
    uses_structured, _struct_evs = _judges_use_structured_output(evidences)
    if not uses_structured and criterion_id in {"structured_output_enforcement", "judicial_nuance"}:
        structured_output_violation = True
        if score > 2:
            score = 2

    # Clamp to 1–5 range.
    score = max(1, min(5, score))

    # Build dialectical dissent / synthesis summary.
    if not opinions:
        return score, None, needs_reeval

    judge_summaries: List[str] = []
    for op in opinions:
        snippet = op.argument.strip().replace("\n", " ")
        if len(snippet) > 140:
            snippet = snippet[:137] + "..."
        judge_summaries.append(f"{op.judge} ({op.score}/5): {snippet}")

    dissent_lines: List[str] = []
    dissent_lines.append("Judicial opinions:")
    for summary in judge_summaries:
        dissent_lines.append(f"- {summary}")

    if defense_overruled:
        missing_descriptions: List[str] = []
        for ev in gap_evidences:
            desc = ev.goal or ev.location
            missing_descriptions.append(desc)
        joined_missing = ", ".join(sorted(set(missing_descriptions))) if missing_descriptions else "required artifacts"
        dissent_lines.append(
            "Defense claim contradicted by repository evidence; overruled. "
            f"DocAnalyst/RepoInvestigator reported missing or inadequate artifacts: {joined_missing}."
        )

    if security_cap is not None and security_evidence:
        locations = sorted({ev.location for ev in security_evidence})
        loc_str = ", ".join(locations)
        dissent_lines.append(
            "Security override applied per Rule of Security: Prosecutor and supporting evidence "
            f"identified high-confidence security vulnerabilities in {loc_str}. Final score is "
            "capped at 3 to reflect this risk even when other judges argued for a higher score."
        )

    if orchestration_fraud:
        dissent_lines.append(
            "Statute of Orchestration: graph analysis evidence indicates a linear or insufficient "
            "fan-out/fan-in structure. The 'Graph Orchestration Architecture' criterion is treated "
            "as Orchestration Fraud and capped at 1 for this rubric dimension."
        )

    if technical_debt_override:
        dissent_lines.append(
            "Statute of Engineering: state management relies on unstructured or non-rigorous state "
            "definitions. The 'State Management Rigor' criterion is assigned a Technical Debt score "
            "of 3 regardless of higher judicial opinions."
        )

    if structured_output_violation:
        dissent_lines.append(
            "Hallucination Liability (Structured Output Enforcement): judges do not enforce "
            "structured JudicialOpinion outputs via tooling, so the relevant rubric score is "
            "capped at 2 to reflect increased hallucination risk."
        )

    if resolution_clauses:
        dissent_lines.append("Conflict resolution rationale:")
        for clause in resolution_clauses:
            dissent_lines.append(f"- {clause}")

    dissent_summary = "\n".join(dissent_lines) if dissent_lines else None
    return score, dissent_summary, needs_reeval


# ---------------------------------------------------------------------------
# Markdown report generation
# ---------------------------------------------------------------------------


def _render_criterion_md(criterion: CriterionResult) -> str:
    """Render a single criterion section in Markdown."""
    lines: List[str] = [
        f"## {criterion.dimension_name}",
        f"- Final Score: {criterion.final_score}/5",
        "- Rationale:",
    ]
    for op in criterion.judge_opinions:
        lines.append(f"  - {op.judge}: {op.argument[:200]}...")
    if criterion.dissent_summary:
        lines.append("- Dissent Summary:")
        lines.append(f"  - {criterion.dissent_summary}")
    lines.append("")
    return "\n".join(lines)


def _build_report_md(
    executive_summary: str,
    criteria_results: List[CriterionResult],
) -> str:
    """
    Build the full Markdown report according to the Layer 3 "Supreme Court"
    specification.
    """
    lines: List[str] = [
        "# Executive Summary",
        "",
        executive_summary,
        "",
        "---",
        "",
        "# Criterion Breakdown",
        "",
    ]

    for criterion in criteria_results:
        lines.append(_render_criterion_md(criterion))

    lines.extend(
        [
            "---",
            "",
            "# Remediation Plan",
            "",
        ]
    )

    # For each failed or weak criterion, surface concrete remediation guidance.
    for criterion in criteria_results:
        if criterion.final_score >= 5:
            continue
        lines.append(f"- {criterion.dimension_name}: {criterion.remediation}")

    if not any(c.final_score < 5 for c in criteria_results):
        lines.append("- All criteria scored 5/5; no remediation required beyond maintaining current practices.")

    lines.extend(
        [
            "",
            "---",
            "",
            "*Report generated by the ChiefJustice node of Automation Auditor.*",
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ChiefJustice node
# ---------------------------------------------------------------------------


@traceable(name="ChiefJustice")  # ensures a distinct trace entry per graph node
def chief_justice_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    ChiefJustice node: collects all JudicialOpinion objects, iterates over
    rubric_dimensions, and produces one CriterionResult per dimension.
    """
    opinions: List[JudicialOpinion] = list(state.get("opinions") or [])
    evidences: Dict[str, List[Evidence]] = state.get("evidences") or {}
    rubric_dimensions: List[Dict[str, Any]] = list(state.get("rubric_dimensions") or [])
    repo_url = state.get("repo_url", "unknown")

    logger.info(
        "ChiefJustice: %d judicial opinions produced, %d rubric dimensions to synthesize",
        len(opinions),
        len(rubric_dimensions),
    )

    # Group opinions by criterion_id (dimension id)
    by_criterion: Dict[str, List[JudicialOpinion]] = {}
    for op in opinions:
        cid = op.criterion_id or "default"
        by_criterion.setdefault(cid, []).append(op)

    # If no rubric dimensions, produce minimal report (backwards compat)
    if not rubric_dimensions:
        executive_summary = (
            "No JudicialOpinion objects were collected from Prosecutor, Defense, "
            "or TechLead. Evidence aggregation or judge execution may have failed."
        )
        empty_report = AuditReport(
            repo_url=repo_url,
            executive_summary=executive_summary,
            overall_score=3.0,
            criteria=[],
            remediation_plan="Re-run the audit with functioning detective and judge layers.",
        )
        report_md = _build_report_md(executive_summary, [])
        report_path = "audit_report.md"
        Path(report_path).write_text(report_md, encoding="utf-8")
        return {
            "final_report": empty_report,
            "final_verdict": empty_report,
            "report_md": report_md,
            "report_path": report_path,
            "needs_reeval": False,
        }

    criteria_results: List[CriterionResult] = []
    all_scores: List[float] = []
    any_reeval = False

    # Iterate over rubric_dimensions (not by_criterion) to ensure one section per dimension.
    for dimension in rubric_dimensions:
        criterion_id = dimension.get("id", "default")
        dimension_name = dimension.get("name", criterion_id.replace("_", " ").title())
        ops = by_criterion.get(criterion_id, [])

        # If no opinions for this dimension, use fallback score 3.
        if not ops:
            score, dissent, needs_reeval = 3, None, False
        else:
            score, dissent, needs_reeval = _compute_final_score(ops, evidences, criterion_id)

        any_reeval = any_reeval or needs_reeval
        all_scores.append(float(score))

        # Build remediation: use cited_evidence from opinions when available.
        referenced_locations: List[str] = []
        cited_handles: List[str] = []
        for op in ops:
            cited_handles.extend(op.cited_evidence)
        cited_handles = [h for h in cited_handles if h]
        referenced_locations.extend(cited_handles)

        for handle in cited_handles:
            handle_lower = handle.lower()
            for source_items in evidences.values():
                for ev in source_items:
                    if handle_lower in ev.location.lower() or handle_lower in ev.goal.lower():
                        referenced_locations.append(ev.location)

        referenced_locations = sorted(set(loc for loc in referenced_locations if loc))

        remediation_parts: List[str] = []
        if _is_security_criterion(criterion_id):
            remediation_parts.append(
                "Review all security-sensitive call sites referenced by the cited evidence. "
                "Replace unsafe patterns such as os.system, eval, or unchecked subprocess calls "
                "with parameterized, validated alternatives (for example, subprocess.run([...], "
                "check=True) using sanitized arguments). Add regression tests that cover invalid "
                "inputs and authentication/authorization edge cases."
            )
        if _is_architecture_criterion(criterion_id):
            remediation_parts.append(
                "Refactor the modules referenced in this criterion to improve modularity and "
                "separation of concerns. Extract reusable components into focused functions or "
                "classes, and ensure that state updates flow through well-defined reducers."
            )

        if not remediation_parts:
            remediation_parts.append(
                "Tighten implementation and tests for this criterion in the files named in the "
                "cited evidence. Prefer small, composable functions, explicit error handling, "
                "and updated documentation that matches the actual behavior."
            )

        if referenced_locations:
            remediation_parts.append(
                "Start by editing and reviewing the following files or evidence locations: "
                + ", ".join(referenced_locations[:5])
            )

        remediation_text = " ".join(remediation_parts)

        criteria_results.append(
            CriterionResult(
                dimension_id=criterion_id,
                dimension_name=dimension_name,
                final_score=score,
                judge_opinions=ops,
                dissent_summary=dissent,
                remediation=remediation_text,
            )
        )

    logger.info("ChiefJustice: synthesized %d criteria", len(criteria_results))

    overall = sum(all_scores) / len(all_scores) if all_scores else 3.0
    n_criteria = len(criteria_results)
    executive_summary_parts: List[str] = [
        f"Repository: {repo_url}.",
        f"Overall final score: {overall:.1f}/5 across {n_criteria} criteria.",
    ]
    if any_reeval:
        executive_summary_parts.append(
            "One or more criteria exhibited score variance > 2 between judges; "
            "deterministic re-evaluation rules were applied, and a manual review "
            "is recommended for those criteria."
        )
    executive_summary = " ".join(executive_summary_parts)

    report_md = _build_report_md(executive_summary, criteria_results)

    final_report = AuditReport(
        repo_url=repo_url,
        executive_summary=executive_summary,
        overall_score=overall,
        criteria=criteria_results,
        remediation_plan=(
            "Review the 'Remediation Plan' section of the generated audit_report.md "
            "and implement the file-level corrections described for low-scoring criteria."
        ),
    )

    report_path = "audit_report.md"
    Path(report_path).write_text(report_md, encoding="utf-8")

    return {
        "final_report": final_report,
        "final_verdict": final_report,
        "report_md": report_md,
        "report_path": report_path,
        "needs_reeval": any_reeval,
    }
