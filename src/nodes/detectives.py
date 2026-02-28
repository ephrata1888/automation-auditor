from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.state import Evidence
from src.tools.doc_tools import extract_path_like_strings, find_keyword_chunks, ingest_pdf
from src.tools.repo_tools import (
    clone_repo_sandboxed,
    extract_git_log,
    find_call_snippet,
    find_method_call_snippet,
    find_symbol_usage_snippet,
    parse_file_ast,
)
from src.tools.vision_tools import analyze_flow as vision_analyze_flow, extract_images_from_pdf


logger = logging.getLogger(__name__)

# Optional LangSmith tracing for node-level observability.
try:  # pragma: no cover - environment specific
    from langsmith import traceable  # type: ignore[import]
except Exception:  # pragma: no cover - environment specific
    def traceable(*_args: Any, **_kwargs: Any):  # type: ignore[no-redef]
        def _decorator(func):
            return func

        return _decorator


class RepoInvestigator:
    """Detective node responsible for inspecting repositories and graph structure.

    This class provides two main capabilities:
    1. Inspect a repository's git history in a sandboxed clone.
    2. Analyze the Automation Auditor graph wiring using the Python AST.
    """

    def _summarize_commit_history_llm(self, commit_log: str) -> Optional[Evidence]:
        """
        Use an LLM to semantically summarize git history for orchestration assessment.

        Returns an Evidence object on success, or None if the LLM is unavailable
        or the call fails. Deterministic fallbacks (existing Evidence) remain primary.
        """
        if not commit_log.strip():
            return None

        try:
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import]
            from langchain_openai import ChatOpenAI  # type: ignore[import]
        except Exception as exc:  # pragma: no cover - import environment specific
            logger.warning("LLM client unavailable for git history summary: %s", exc)
            return None

        system_prompt = (
            "You are assisting an Automation Auditor that evaluates repository "
            "architecture and development process.\n\n"
            "Given a chronological git log (oldest first) in the format "
            "'<hash> <timestamp> <message>', produce a concise summary focused on:\n"
            "- Whether commits show progression: environment setup -> tooling -> graph orchestration.\n"
            "- Any signs of bulk upload / single-shot commit patterns.\n"
            "- How well the history supports a parallel StateGraph architecture."
        )

        user_prompt = (
            "Git log (oldest to newest):\n\n"
            f"{commit_log}\n\n"
            "Respond with 2–4 sentences. Be factual and avoid speculation."
        )

        try:
            llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
            msg = llm.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )
            content = getattr(msg, "content", "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM git history summary failed: %s", exc)
            return None

        summary = str(content).strip()
        if not summary:
            return None

        return Evidence(
            goal="LLM summary: git history for orchestration",
            found=True,
            content=summary,
            location="git log --oneline",
            rationale=(
                "LLM-provided semantic summary of commit progression and its "
                "relevance to graph orchestration quality."
            ),
            confidence=0.7,
        )

    def extract_git_history(self, path: str) -> List[Evidence]:
        """Clone a git repository and extract its commit history.

        The repository located at ``path`` (typically a git URL or local path)
        is cloned into a sandboxed temporary directory. A simplified git log
        containing commit hashes, timestamps, and messages is collected and
        turned into a single Evidence record describing whether the repository
        exhibits meaningful git history.

        Args:
            path: Git repository URL or local path.

        Returns:
            A list containing a single Evidence object that summarizes the
            repository's commit history, or an error-focused Evidence if the
            git operations fail.
        """
        evidences: List[Evidence] = []

        try:
            clone_target = clone_repo_sandboxed(path)
        except RuntimeError as exc:
            logger.debug("Repository clone failed: %s", exc)
            evidences.append(
                Evidence(
                    goal="Repo has meaningful git history",
                    found=False,
                    content=str(exc),
                    location="git clone",
                    rationale=(
                        "Failed to execute git clone, so the commit history "
                        "could not be inspected."
                    ),
                    confidence=0.2,
                )
            )
            return evidences

        try:
            commit_log = extract_git_log(clone_target)
        except RuntimeError as exc:
            logger.debug("Extracting git log failed: %s", exc)
            evidences.append(
                Evidence(
                    goal="Repo has meaningful git history",
                    found=False,
                    content=str(exc),
                    location="git log --oneline",
                    rationale=(
                        "git log reported an error, which prevents assessment "
                        "of the repository's commit history."
                    ),
                    confidence=0.3,
                )
            )
            return evidences

        commits = [line for line in commit_log.splitlines() if line.strip()]
        commit_count = len(commits)
        found = commit_count > 3

        if commit_count == 0:
            rationale = (
                "The repository has no recorded commits in its history, which "
                "does not demonstrate stepwise development."
            )
            confidence = 0.9
        elif found:
            rationale = (
                f"The repository has {commit_count} commits, which suggests a "
                "stepwise development process with meaningful history."
            )
            confidence = 0.85 if commit_count <= 10 else 0.95
        else:
            rationale = (
                f"The repository has only {commit_count} commits, which may be "
                "insufficient to demonstrate meaningful, incremental development."
            )
            confidence = 0.7

        evidences.append(
            Evidence(
                goal="Repo has meaningful git history",
                found=found,
                content=commit_log or None,
                location="git log --oneline",
                rationale=rationale,
                confidence=confidence,
            )
        )

        # Optional LLM semantic summary (does not affect primary Evidence)
        llm_evidence = self._summarize_commit_history_llm(commit_log)
        if llm_evidence is not None:
            evidences.append(llm_evidence)

        return evidences

    def analyze_graph_structure(self, path: str) -> List[Evidence]:
        """Analyze the Automation Auditor graph wiring via static AST inspection.

        This method inspects key source files (notably ``src/graph.py`` and
        ``src/state.py``) under the provided repository root. It uses Python's
        ``ast`` module to verify:

        - That a ``StateGraph`` instantiation exists.
        - That calls to ``builder.add_edge`` and ``builder.add_conditional_edges``
          are present.
        - That node symbols such as ``RepoInvestigator``, ``DocAnalyst``, and
          ``EvidenceAggregator`` appear in the graph definition.

        Args:
            path: Root directory of the repository checkout.

        Returns:
            A list of Evidence objects describing each structural check and whether
            it was satisfied.
        """
        root = Path(path)
        graph_path = root / "src" / "graph.py"
        state_path = root / "src" / "state.py"

        evidences: List[Evidence] = []

        graph_source: Optional[str]
        state_source: Optional[str]

        try:
            graph_source = graph_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Failed to read graph file %s: %s", graph_path, exc)
            graph_source = None

        try:
            state_source = state_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Failed to read state file %s: %s", state_path, exc)
            state_source = None

        # Parse the state module (if we could read it) regardless of what happens
        # with the graph module, so we can still report state parsing issues even
        # when the graph file is missing or unreadable.
        state_module = parse_file_ast(state_path) if state_source is not None else None

        # If we could not read the graph source at all, report that explicitly
        # instead of silently treating it as an empty graph.
        if graph_source is None:
            evidences.append(
                Evidence(
                    goal="Graph module is readable",
                    found=False,
                    content=None,
                    location=str(graph_path),
                    rationale=(
                        "src/graph.py could not be read from disk, so its structure "
                        "cannot be analyzed. This may indicate the file is missing, "
                        "unreadable, or encoded in an unexpected format."
                    ),
                    confidence=0.7,
                )
            )

            # Even though the graph cannot be read, still report on whether the
            # state module parses correctly when it is present.
            if state_source is not None:
                evidences.append(
                    Evidence(
                        goal="State module parses correctly",
                        found=state_module is not None,
                        content=None,
                        location=str(state_path),
                        rationale=(
                            "src/state.py was successfully parsed by the AST module."
                            if state_module is not None
                            else "src/state.py exists but could not be parsed by the "
                            "AST module, which may indicate syntax errors."
                        ),
                        confidence=0.9 if state_module is not None else 0.7,
                    )
                )

            return evidences

        graph_module = parse_file_ast(graph_path) if graph_source is not None else None

        # If the graph module fails to parse, report that explicitly rather than
        # silently marking all structural checks as missing.
        if graph_module is None and graph_source is not None:
            evidences.append(
                Evidence(
                    goal="Graph module parses correctly",
                    found=False,
                    content=None,
                    location=str(graph_path),
                    rationale=(
                        "src/graph.py exists but could not be parsed by the AST "
                        "module, which may indicate syntax errors. Graph structure "
                        "checks cannot be reliably evaluated."
                    ),
                    confidence=0.7,
                )
            )
            return evidences

        # --- Check 1: StateGraph instantiation exists ---
        stategraph_snippet = (
            find_call_snippet(
                module=graph_module,
                source=graph_source,
                func_names={"StateGraph"},
            )
            if graph_module is not None and graph_source is not None
            else None
        )

        evidences.append(
            Evidence(
                goal="StateGraph is instantiated in graph definition",
                found=stategraph_snippet is not None,
                content=stategraph_snippet,
                location=str(graph_path),
                rationale=(
                    "A call to StateGraph() in src/graph.py confirms that the "
                    "state graph is explicitly instantiated."
                    if stategraph_snippet is not None
                    else "No call to StateGraph() was found in src/graph.py, so the "
                    "state graph wiring may be incomplete or defined elsewhere."
                ),
                confidence=0.9 if stategraph_snippet is not None else 0.5,
            )
        )

        # --- Check 2: builder.add_edge() exists ---
        add_edge_snippet = (
            find_method_call_snippet(
                module=graph_module,
                source=graph_source,
                receiver_name="builder",
                method_name="add_edge",
            )
            if graph_module is not None and graph_source is not None
            else None
        )

        evidences.append(
            Evidence(
                goal="Graph has builder.add_edge calls",
                found=add_edge_snippet is not None,
                content=add_edge_snippet,
                location=str(graph_path),
                rationale=(
                    "Found calls to builder.add_edge(), indicating explicit edges "
                    "between nodes in the Automation Auditor graph."
                    if add_edge_snippet is not None
                    else "No calls to builder.add_edge() were found; the graph may "
                    "not define explicit edges between nodes."
                ),
                confidence=0.9 if add_edge_snippet is not None else 0.6,
            )
        )

        # --- Check 3: builder.add_conditional_edges() exists ---
        add_conditional_snippet = (
            find_method_call_snippet(
                module=graph_module,
                source=graph_source,
                receiver_name="builder",
                method_name="add_conditional_edges",
            )
            if graph_module is not None and graph_source is not None
            else None
        )

        evidences.append(
            Evidence(
                goal="Graph has builder.add_conditional_edges calls",
                found=add_conditional_snippet is not None,
                content=add_conditional_snippet,
                location=str(graph_path),
                rationale=(
                    "Found calls to builder.add_conditional_edges(), indicating "
                    "that conditional transitions are modeled in the graph."
                    if add_conditional_snippet is not None
                    else "No calls to builder.add_conditional_edges() were found; "
                    "conditional transitions may not be explicitly represented."
                ),
                confidence=0.9 if add_conditional_snippet is not None else 0.6,
            )
        )

        # --- Check 4: Node symbols exist in the graph wiring ---
        node_names = ["RepoInvestigator", "DocAnalyst", "EvidenceAggregator"]
        for node_name in node_names:
            snippet = (
                find_symbol_usage_snippet(
                    module=graph_module,
                    source=graph_source,
                    symbol=node_name,
                )
                if graph_module is not None and graph_source is not None
                else None
            )

            evidences.append(
                Evidence(
                    goal=f"Graph references node {node_name}",
                    found=snippet is not None,
                    content=snippet,
                    location=str(graph_path),
                    rationale=(
                        f"Found {node_name} referenced in src/graph.py, suggesting "
                        "it participates in the Automation Auditor graph."
                        if snippet is not None
                        else f"Did not find {node_name} referenced in src/graph.py; "
                        "the node may be missing from the graph wiring."
                    ),
                    confidence=0.9 if snippet is not None else 0.6,
                )
            )

        # --- Check 5: State management rigor in AgentState ---
        if state_source is not None:
            agentstate_snippet = None
            if state_module is not None:
                # Find the AgentState class definition by simple source search.
                marker = "class AgentState"
                idx = state_source.find(marker)
                if idx != -1:
                    # Take a small window around the declaration.
                    lines = state_source.splitlines()
                    for i, line in enumerate(lines):
                        if line.strip().startswith(marker):
                            start = max(i - 1, 0)
                            end = min(i + 8, len(lines))
                            agentstate_snippet = "\n".join(lines[start:end])
                            break

            has_evidences = "evidences: Annotated[" in (agentstate_snippet or "")
            has_opinions = "opinions: Annotated[" in (agentstate_snippet or "")
            found_state_rigor = bool(agentstate_snippet and has_evidences and has_opinions)

            evidences.append(
                Evidence(
                    goal="AgentState maintains evidences and judicial opinions",
                    found=found_state_rigor,
                    content=agentstate_snippet,
                    location=str(state_path),
                    rationale=(
                        "AgentState defines evidences and opinions fields backed by "
                        "Evidence and JudicialOpinion collections."
                        if found_state_rigor
                        else "AgentState could not be fully validated as maintaining "
                        "both evidences and judicial opinions."
                    ),
                    confidence=0.9 if found_state_rigor else 0.6,
                )
            )

        # --- Check 6: Graph fan-out and fan-in patterns ---
        if graph_module is not None:
            edges: dict[str, list[str]] = {}

            for node in ast.walk(graph_module):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "builder":
                        if node.func.attr in {"add_edge", "add_conditional_edges"} and node.args:
                            # Best-effort: assume first two positional args are from_node, to_node.
                            if len(node.args) >= 2:
                                from_arg, to_arg = node.args[0], node.args[1]
                                from_label = getattr(from_arg, "id", None) or getattr(
                                    from_arg, "s", None
                                )
                                to_label = getattr(to_arg, "id", None) or getattr(
                                    to_arg, "s", None
                                )
                                if isinstance(from_label, str) and isinstance(to_label, str):
                                    edges.setdefault(from_label, []).append(to_label)

            has_fan_out = any(len(targets) > 1 for targets in edges.values())

            reverse_edges: dict[str, list[str]] = {}
            for src_node, targets in edges.items():
                for tgt in targets:
                    reverse_edges.setdefault(tgt, []).append(src_node)
            has_fan_in = any(len(sources) > 1 for sources in reverse_edges.values())

            fanout_snippet = None
            fanin_snippet = None
            if (has_fan_out or has_fan_in) and graph_source is not None:
                fanout_snippet = graph_source
                fanin_snippet = graph_source

            evidences.append(
                Evidence(
                    goal="Graph models fan-out from nodes",
                    found=has_fan_out,
                    content=fanout_snippet,
                    location=str(graph_path),
                    rationale=(
                        "At least one node has multiple outgoing edges, indicating "
                        "fan-out to parallel nodes."
                        if has_fan_out
                        else "No nodes were detected with multiple outgoing edges; "
                        "parallel fan-out may be absent or not statically detectable."
                    ),
                    confidence=0.8 if has_fan_out else 0.6,
                )
            )

            evidences.append(
                Evidence(
                    goal="Graph models fan-in to aggregator nodes",
                    found=has_fan_in,
                    content=fanin_snippet,
                    location=str(graph_path),
                    rationale=(
                        "At least one node receives edges from multiple sources, "
                        "indicating fan-in / synchronization."
                        if has_fan_in
                        else "No nodes were detected with multiple incoming edges; "
                        "fan-in may be absent or not statically detectable."
                    ),
                    confidence=0.8 if has_fan_in else 0.6,
                )
            )

        # --- Check 7: Safe tool engineering for repository cloning ---
        tools_root = root / "src" / "tools" / "repo_tools.py"
        try:
            tools_source = tools_root.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Failed to read repo_tools.py for safety check: %s", exc)
            tools_source = None

        if tools_source is not None:
            has_clone_function = "def clone_repo_sandboxed" in tools_source
            uses_tempfile = "tempfile.mkdtemp" in tools_source
            uses_subprocess = "subprocess.run" in tools_source
            uses_os_system = "os.system(" in tools_source

            safe_tooling = has_clone_function and uses_tempfile and uses_subprocess and not uses_os_system

            snippet = None
            if has_clone_function:
                lines = tools_source.splitlines()
                for i, line in enumerate(lines):
                    if line.strip().startswith("def clone_repo_sandboxed"):
                        start = max(i - 1, 0)
                        end = min(i + 15, len(lines))
                        snippet = "\n".join(lines[start:end])
                        break

            evidences.append(
                Evidence(
                    goal="Repo uses sandboxed clone tooling",
                    found=safe_tooling,
                    content=snippet,
                    location=str(tools_root),
                    rationale=(
                        "clone_repo_sandboxed uses tempfile and subprocess.run and no os.system "
                        "calls were detected."
                        if safe_tooling
                        else "Repository tooling for cloning may not be fully sandboxed or safe "
                        "(missing tempfile/subprocess usage or presence of os.system)."
                    ),
                    confidence=0.9 if safe_tooling else 0.6,
                )
            )

        # We currently only inspect src/state.py to ensure it parses, but we do not
        # execute it or import any untrusted code.
        if state_source is not None:
            evidences.append(
                Evidence(
                    goal="State module parses correctly",
                    found=state_module is not None,
                    content=None,
                    location=str(state_path),
                    rationale=(
                        "src/state.py was successfully parsed by the AST module."
                        if state_module is not None
                        else "src/state.py exists but could not be parsed by the AST "
                        "module, which may indicate syntax errors."
                    ),
                    confidence=0.9 if state_module is not None else 0.7,
                )
            )

        # --- Check 8: Judge LLMs use structured JudicialOpinion output ---
        judges_path = root / "src" / "nodes" / "judges.py"
        try:
            judges_source = judges_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Failed to read judges.py for structured output check: %s", exc)
            judges_source = None

        if judges_source is not None:
            has_structured_call = ".with_structured_output(JudicialOpinion" in judges_source

            snippet = None
            if has_structured_call:
                lines = judges_source.splitlines()
                for i, line in enumerate(lines):
                    if "with_structured_output(JudicialOpinion" in line:
                        start = max(i - 2, 0)
                        end = min(i + 5, len(lines))
                        snippet = "\n".join(lines[start:end])
                        break

            evidences.append(
                Evidence(
                    goal="Judge LLM calls are structured to JudicialOpinion",
                    found=has_structured_call,
                    content=snippet,
                    location=str(judges_path),
                    rationale=(
                        "Judges configure their LLM client with with_structured_output(JudicialOpinion) "
                        "to ensure outputs conform to the JudicialOpinion schema."
                        if has_structured_call
                        else "No use of with_structured_output(JudicialOpinion) was detected in judges.py; "
                        "judge LLM calls may not be using structured outputs."
                    ),
                    confidence=0.9 if has_structured_call else 0.6,
                )
            )

        return evidences


class DocAnalyst:
    """Detective node responsible for analyzing PDF reports and documentation.

    This class focuses on two forensic protocols:

    1. Citation checks: verifying that files cited in a report actually exist.
    2. Concept verification: checking that key concepts are meaningfully
       explained within a PDF document.
    """

    def check_citations(self, repo_root: str, claimed_paths: List[str]) -> List[Evidence]:
        """Verify that each claimed file path exists in the repository.

        Args:
            repo_root: Root directory of the repository being audited.
            claimed_paths: List of file paths as claimed in the report, relative
                to the repository root (e.g., ``src/nodes/judges.py``).

        Returns:
            A list of Evidence objects, one per claimed path.
        """
        evidences: List[Evidence] = []
        root = Path(repo_root)

        for rel_path in claimed_paths:
            file_path = root / rel_path
            exists = file_path.is_file()

            rationale: str
            if exists:
                rationale = (
                    "The cited file path exists in the repository, so the report's "
                    "citation appears valid."
                )
                confidence = 0.95
            else:
                rationale = (
                    "No file exists at the cited path in the repository; this "
                    "appears to be a hallucinated or incorrect citation."
                )
                confidence = 0.7

            evidences.append(
                Evidence(
                    goal=f"File exists: {rel_path}",
                    found=exists,
                    content=str(rel_path) if exists else None,
                    location=str(file_path),
                    rationale=rationale,
                    confidence=confidence,
                )
            )

        return evidences

    def _llm_assess_concept_depth(
        self,
        pdf_path: str,
        keyword: str,
        explanatory_chunks: List[str],
    ) -> Optional[Evidence]:
        """
        Use an LLM to semantically assess whether the PDF's explanation of a
        theoretical concept is deep and aligned with rubric expectations.

        Returns an Evidence object on success, or None on failure.
        """
        if not explanatory_chunks:
            return None

        try:
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import]
            from langchain_openai import ChatOpenAI  # type: ignore[import]
        except Exception as exc:  # pragma: no cover - import environment specific
            logger.warning("LLM client unavailable for concept depth check: %s", exc)
            return None

        joined_chunks = "\n\n".join(explanatory_chunks[:3])

        system_prompt = (
            "You are assisting an Automation Auditor evaluating a PDF report. "
            "You must assess whether a theoretical concept is explained with "
            "sufficient depth and architectural specificity.\n\n"
            "Consider:\n"
            "- Does the text describe HOW the concept is implemented, not just name it?\n"
            "- Is the explanation tied to concrete architectural elements (e.g., "
            "StateGraph nodes, fan-in/fan-out edges, judges, or state management)?\n"
            "Respond with a short verdict and justification."
        )

        user_prompt = (
            f"Concept keyword: {keyword}\n\n"
            "Relevant excerpts from the PDF:\n\n"
            f"{joined_chunks}\n\n"
            "Answer in 2–3 sentences. Start your answer with either "
            "'DEPTH: STRONG' or 'DEPTH: WEAK', then explain why."
        )

        try:
            llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
            msg = llm.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )
            content = getattr(msg, "content", "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM concept depth assessment failed for %s: %s", keyword, exc)
            return None

        verdict = str(content).strip()
        if not verdict:
            return None

        is_strong = verdict.upper().startswith("DEPTH: STRONG")
        confidence = 0.8 if is_strong else 0.7

        return Evidence(
            goal=f"LLM assessment: concept depth for {keyword}",
            found=is_strong,
            content=verdict,
            location=pdf_path,
            rationale=(
                "LLM semantic assessment of how deeply the PDF explains the "
                f"concept '{keyword}' in terms of concrete architecture."
            ),
            confidence=confidence,
        )

    def verify_concepts(self, pdf_path: str, keywords: List[str]) -> List[Evidence]:
        """Assess whether key concepts are meaningfully explained in a PDF.

        The PDF is ingested into text chunks, and each keyword is searched for
        within those chunks. A concept is considered explained when it appears
        in a paragraph that contains additional descriptive context (beyond the
        keyword itself).

        Args:
            pdf_path: Path to the PDF report.
            keywords: List of concept keywords to verify.

        Returns:
            A list of Evidence objects, one per keyword.
        """
        evidences: List[Evidence] = []
        chunks = ingest_pdf(pdf_path)
        # If ingestion fails, we still produce Evidence but with lower confidence.
        if not chunks:
            for keyword in keywords:
                evidences.append(
                    Evidence(
                        goal=f"Concept explained: {keyword}",
                        found=False,
                        content=None,
                        location=pdf_path,
                        rationale=(
                            "Unable to extract textual content from the PDF, so the "
                            "presence and depth of this concept cannot be verified."
                        ),
                        confidence=0.6,
                    )
                )
            return evidences

        for keyword in keywords:
            # Find all chunks that contain this specific keyword.
            keyword_chunks = find_keyword_chunks(chunks, [keyword])

            if not keyword_chunks:
                evidences.append(
                    Evidence(
                        goal=f"Concept explained: {keyword}",
                        found=False,
                        content=None,
                        location=pdf_path,
                        rationale=(
                            "The keyword does not appear in the parsed PDF text, "
                            "so the concept does not seem to be discussed."
                        ),
                        confidence=0.6,
                    )
                )
                continue

            explanatory_chunks: List[str] = []
            for chunk in keyword_chunks:
                # Heuristic: treat a chunk as explanatory when it contains
                # sufficient surrounding context beyond the keyword itself.
                token_count = len(chunk.split())
                if token_count >= 10:
                    explanatory_chunks.append(chunk)

            if explanatory_chunks:
                evidences.append(
                    Evidence(
                        goal=f"Concept explained: {keyword}",
                        found=True,
                        content="\n\n".join(explanatory_chunks),
                        location=pdf_path,
                        rationale=(
                            "The concept appears in one or more paragraphs with "
                            "substantive surrounding text, suggesting a meaningful "
                            "explanation rather than a passing mention."
                        ),
                        confidence=0.9,
                    )
                )

                # Optional LLM semantic depth check (non-breaking enhancement)
                llm_ev = self._llm_assess_concept_depth(
                    pdf_path=pdf_path,
                    keyword=keyword,
                    explanatory_chunks=explanatory_chunks,
                )
                if llm_ev is not None:
                    evidences.append(llm_ev)
            else:
                evidences.append(
                    Evidence(
                        goal=f"Concept explained: {keyword}",
                        found=False,
                        content="\n\n".join(keyword_chunks),
                        location=pdf_path,
                        rationale=(
                            "The keyword appears in the PDF but only in short or "
                            "context-poor fragments, suggesting a shallow mention "
                            "rather than a full explanation."
                        ),
                        confidence=0.6,
                    )
                )

        return evidences

    KEY_THEORETICAL_CONCEPTS = [
        "Dialectical Synthesis",
        "Fan-In / Fan-Out",
        "Metacognition",
        "State Synchronization",
    ]

    def verify_theoretical_depth(self, pdf_path: str) -> List[Evidence]:
        """Evaluate whether rubric-specific theoretical concepts are explained in the PDF."""
        return self.verify_concepts(pdf_path, self.KEY_THEORETICAL_CONCEPTS)

    def analyze_host_references(self, repo_root: str, pdf_path: str) -> List[Evidence]:
        """Extract file-like paths from a PDF and verify them against the repository."""
        chunks = ingest_pdf(pdf_path)
        if not chunks:
            return []

        path_candidates = extract_path_like_strings(chunks)
        if not path_candidates:
            return []

        return self.check_citations(repo_root, path_candidates)


class VisionInspector:
    """Detective node responsible for inspecting diagrams in PDF reports.

    This node focuses on vision-centric analysis of architectural diagrams, with
    an emphasis on verifying that the depicted flow matches the Automation
    Auditor design (parallel detectives and judges around an evidence
    aggregation core) rather than a simplistic linear pipeline.
    """

    def extract_images_from_pdf(self, path: str) -> List[Path]:
        """Extract all images from a PDF using the vision tools module."""
        return extract_images_from_pdf(path)

    def analyze_flow(self, image_path: Path) -> str:
        """Analyze a single diagram image using a multimodal vision model.

        This delegates to the helper in ``src.tools.vision_tools`` so that
        integration with Gemini Pro Vision / GPT-4o can be implemented in one
        place.
        """
        return vision_analyze_flow(image_path)

    def inspect_pdf_diagrams(self, pdf_path: str) -> List[Evidence]:
        """Inspect diagrams in a PDF report and generate flow-related evidence.

        This method performs a lightweight RAG-style inspection over the visual
        content of the report:

        1. Extract all images from the PDF.
        2. For each image, ask a multimodal model whether the diagram appears to
           be a StateGraph-style diagram or simple boxes, and whether the flow
           follows ``Detectives (Parallel) -> Evidence Aggregation -> "
           "Judges (Parallel) -> Synthesis``.
        3. Wrap the model's responses into Evidence objects for downstream use.
        """
        evidences: List[Evidence] = []

        image_paths = self.extract_images_from_pdf(pdf_path)
        if not image_paths:
            evidences.append(
                Evidence(
                    goal=(
                        "Report diagrams show Evidence Aggregation -> Judges -> "
                        "Chief Justice flow"
                    ),
                    found=False,
                    content=None,
                    location=pdf_path,
                    rationale=(
                        "No extractable images were found in the PDF, or image "
                        "extraction failed, so the presence and structure of "
                        "architectural diagrams cannot be verified."
                    ),
                    confidence=0.6,
                )
            )
            return evidences

        for image_path in image_paths:
            try:
                analysis_text = self.analyze_flow(image_path)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Vision analysis failed for %s: %s", image_path, exc)
                evidences.append(
                    Evidence(
                        goal=(
                            "Report diagrams show Evidence Aggregation -> Judges -> "
                            "Chief Justice flow"
                        ),
                        found=False,
                        content=None,
                        location=str(image_path),
                        rationale=(
                            "The vision model call failed for this diagram image, so "
                            "its structure could not be analyzed."
                        ),
                        confidence=0.5,
                    )
                )
                continue

            normalized = analysis_text.lower()
            # Rough classification based on keywords in the model's description.
            if "stategraph" in normalized or "state graph" in normalized:
                diagram_type = "stategraph"
            elif "sequence" in normalized:
                diagram_type = "sequence"
            elif "flowchart" in normalized or "box-and-arrow" in normalized:
                diagram_type = "flowchart"
            else:
                diagram_type = "other"

            has_swarm_flow = (
                "evidence aggregation" in normalized
                and (
                    "prosecutor" in normalized
                    or "defense" in normalized
                    or "techlead" in normalized
                    or "tech lead" in normalized
                )
                and ("chief justice" in normalized or "synthesis" in normalized)
            )

            # Evidence: diagram type classification.
            evidences.append(
                Evidence(
                    goal="Diagram type classification (Swarm Visual)",
                    found=True,
                    content=f"type={diagram_type}\n\n{analysis_text}",
                    location=str(image_path),
                    rationale=(
                        f"The vision model description suggests this is a {diagram_type} diagram."
                    ),
                    confidence=0.8,
                )
            )

            # Evidence: Swarm flow presence.
            evidences.append(
                Evidence(
                    goal=(
                        "Diagram models Evidence Aggregation -> (Prosecutor|Defense|TechLead) "
                        "-> Chief Justice/Synthesis flow"
                    ),
                    found=has_swarm_flow,
                    content=analysis_text,
                    location=str(image_path),
                    rationale=(
                        "The diagram description includes Evidence Aggregation flowing to "
                        "Prosecutor/Defense/TechLead and then to Chief Justice or Synthesis."
                        if has_swarm_flow
                        else "The diagram description does not clearly include the full "
                        "Evidence Aggregation -> Judges -> Chief Justice/Synthesis flow."
                    ),
                    confidence=0.85 if has_swarm_flow else 0.6,
                )
            )

        return evidences


# ---------------------------------------------------------------------------
# Graph node wrappers (StateGraph detectives)
# ---------------------------------------------------------------------------


@traceable(name="RepoInvestigator")  # ensures a distinct trace entry per graph node
def repo_investigator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    RepoInvestigator node: inspects repository and graph structure.
    Returns evidences keyed by "RepoInvestigator"; sets repo_error on failure.
    """
    repo_url = state.get("repo_url") or "."
    inv = RepoInvestigator()
    evidences: List[Evidence] = []
    try:
        evidences.extend(inv.extract_git_history(repo_url))
        if Path(repo_url).exists():
            evidences.extend(inv.analyze_graph_structure(repo_url))
    except Exception as exc:  # noqa: BLE001
        logger.exception("RepoInvestigator failed")
        evidences.append(
            Evidence(
                goal="RepoInvestigator execution",
                found=False,
                content=str(exc),
                location=repo_url,
                rationale="Detective raised an exception.",
                confidence=0.1,
            )
        )
        return {"evidences": {"RepoInvestigator": evidences}, "repo_error": True}
    return {"evidences": {"RepoInvestigator": evidences}}


@traceable(name="DocAnalyst")  # ensures a distinct trace entry per graph node
def doc_analyst_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    DocAnalyst node: analyzes PDF report and documentation.
    Returns evidences keyed by "DocAnalyst"; sets doc_error on failure.
    """
    pdf_path = state.get("pdf_path", "")
    repo_url = state.get("repo_url") or "."
    analyst = DocAnalyst()
    evidences: List[Evidence] = []
    try:
        evidences.extend(analyst.verify_theoretical_depth(pdf_path))
        evidences.extend(analyst.analyze_host_references(repo_url, pdf_path))
    except Exception as exc:  # noqa: BLE001
        logger.exception("DocAnalyst failed")
        evidences.append(
            Evidence(
                goal="DocAnalyst execution",
                found=False,
                content=str(exc),
                location=pdf_path,
                rationale="Detective raised an exception.",
                confidence=0.1,
            )
        )
        return {"evidences": {"DocAnalyst": evidences}, "doc_error": True}
    return {"evidences": {"DocAnalyst": evidences}}


@traceable(name="VisionInspector")  # ensures a distinct trace entry per graph node
def vision_inspector_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    VisionInspector node: inspects diagrams in PDF report.
    Returns evidences keyed by "VisionInspector"; sets vision_error on failure.
    """
    pdf_path = state.get("pdf_path", "")
    inspector = VisionInspector()
    evidences: List[Evidence] = []
    try:
        evidences.extend(inspector.inspect_pdf_diagrams(pdf_path))
    except Exception as exc:  # noqa: BLE001
        logger.exception("VisionInspector failed")
        evidences.append(
            Evidence(
                goal="VisionInspector execution",
                found=False,
                content=str(exc),
                location=pdf_path,
                rationale="Detective raised an exception.",
                confidence=0.1,
            )
        )
        return {"evidences": {"VisionInspector": evidences}, "vision_error": True}
    return {"evidences": {"VisionInspector": evidences}}


@traceable(name="ErrorHandler")  # ensures a distinct trace entry per graph node
def error_handler_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    ErrorHandler node: logs errors and continues (no-op state update).
    """
    logger.warning(
        "ErrorHandler invoked: repo_error=%s, doc_error=%s, vision_error=%s",
        state.get("repo_error"),
        state.get("doc_error"),
        state.get("vision_error"),
    )
    return {}

