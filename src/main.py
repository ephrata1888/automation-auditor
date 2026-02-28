"""
Minimal entrypoint to run the Automation Auditor graph.

Compiles the audit_graph from src.graph, loads the rubric, and invokes the graph
with the provided repo_url and pdf_path. The ChiefJustice node writes
audit_report.md to the current working directory.

Environment variables (used when args not provided):
  REPO_URL   – Repository URL or path to clone/inspect
  PDF_PATH  – Path to the PDF report

API keys (passed through environment, not modified here):
  OPENAI_API_KEY  – Used by Judges and optional Detective LLMs
  GEMINI_API_KEY  – Optional; used by VisionInspector for diagram analysis
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Resolve project root for rubric.json (works from repo root or /app in Docker).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)
logger = logging.getLogger(__name__)


def _load_rubric_dimensions() -> list[dict]:
    """Load rubric dimensions from rubric.json."""
    rubric_path = _PROJECT_ROOT / "rubric.json"
    if not rubric_path.is_file():
        logger.error("rubric.json not found at %s", rubric_path)
        sys.exit(1)
    with open(rubric_path, encoding="utf-8") as f:
        data = json.load(f)
    dimensions = data.get("dimensions")
    if not dimensions:
        logger.error("rubric.json has no 'dimensions' key")
        sys.exit(1)
    return dimensions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Automation Auditor graph (audit_graph)."
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("REPO_URL", "."),
        help="Repository URL or path (default: REPO_URL or '.')",
    )
    parser.add_argument(
        "--report",
        default=os.environ.get("PDF_PATH", ""),
        help="Path to the PDF report (default: PDF_PATH env)",
    )
    args = parser.parse_args()

    repo_url = args.repo.strip() or "."
    pdf_path = args.report.strip()

    rubric_dimensions = _load_rubric_dimensions()
    logger.info("Loaded %d rubric dimensions from rubric.json", len(rubric_dimensions))

    from src.graph import audit_graph

    graph = audit_graph.compile()
    initial_state = {
        "repo_url": repo_url,
        "pdf_path": pdf_path,
        "rubric_dimensions": rubric_dimensions,
    }

    logger.info("Invoking audit_graph (repo=%s, report=%s)", repo_url, pdf_path or "(none)")
    result = graph.invoke(initial_state)

    report_path = result.get("report_path") or "audit_report.md"
    final_report = result.get("final_report") or result.get("final_verdict")
    n_criteria = len(final_report.criteria) if final_report and hasattr(final_report, "criteria") else 0
    logger.info("Audit complete. Report written to %s (%d criteria synthesized)", report_path, n_criteria)


if __name__ == "__main__":
    main()
