# Automation Auditor

A modular, multi-agent architecture for auditing software repositories and accompanying PDF reports. The system orchestrates parallel "Detective" agents, aggregates structured evidence, and prepares for a judicial synthesis layer.

---

## Architecture Overview

The system is built around a LangGraph `StateGraph` architecture with explicit fan-out and fan-in synchronization points and a deterministic judicial synthesis layer.

### Components

- **Detectives (Parallel Fan-Out)**
  - `RepoInvestigator` – Repository structure, git history, safe sandboxed cloning, and AST-based graph/state inspection.
  - `DocAnalyst` – PDF ingestion and chunked querying (RAG-lite) for theoretical depth and host path accuracy.
  - `VisionInspector` – Diagram extraction and multimodal inspection (vision model stub + rubric-aligned flow checks).
- **EvidenceAggregator (Fan-In)**
  - Collects and merges structured `Evidence` objects using typed reducers on `AgentState`.
- **Judges (Parallel Fan-Out)**
  - `Prosecutor`, `Defense`, `TechLead` – persona-specific LLM evaluators that emit `JudicialOpinion` objects
    (score, argument, cited_evidence) over the same shared `Evidence`.
- **ChiefJustice (Final Fan-In + Deterministic Synthesis)**
  - Applies hardcoded, rule-based overrides aligned with the Automation Auditor Input Rubric:
    - **Rule of Security** – confirmed security flaws cap the score at 3.
    - **Rule of Evidence (Fact Supremacy)** – Detective evidence overrules hallucinated Defense claims.
    - **Rule of Functionality** – TechLead’s modular-architecture confirmation carries highest weight for architecture criteria.
    - **Statutes** – Orchestration Fraud, Technical Debt, and Structured-Output / Hallucination Liability caps.
  - Produces a final `AuditReport` and a Markdown audit file (`audit_report.md`) with Executive Summary, Criterion Breakdown, and Remediation Plan.

---

## Project Structure

```
src/
  state.py
  graph.py
  nodes/
    detectives.py
    judges.py
  tools/
    repo_tools.py
    doc_tools.py
    vision_tools.py

```

- `state.py` – Typed AgentState and reducers for parallel execution.
- `graph.py` – Full StateGraph wiring with Detective fan-out/fan-in and Judge fan-out/fan-in into ChiefJustice.
- `repo_tools.py` – Sandboxed git cloning and log extraction.
- `doc_tools.py` – PDF parsing and chunked querying.
- `vision_tools.py` – Image extraction and multimodal model stub.

---

## Installation

Clone the repository:

```bash
git clone https://github.com/ephrata1888/automation-auditor.git
cd automation-auditor

```

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

```

Install dependencies:

```bash
pip install -r requirements.txt

```

---

## Environment Setup

Copy the environment template:

```bash
cp .env.example .env

```

Fill in any required API keys (for future multimodal model integration).

---

## Example Usage

### Audit a Public Repository

```bash
python -m src.main \
  --repo https://github.com/example/project \
  --report path/to/report.pdf

```

What happens internally:

1. Repository is cloned in a sandboxed temporary directory.
2. Detectives execute in parallel.
3. Evidence is aggregated using typed reducers on `AgentState`.
4. Judges evaluate the same structured evidence in parallel, emitting `JudicialOpinion` objects.
5. ChiefJustice applies deterministic rubric rules and writes `audit_report.md` plus a structured `AuditReport`.

---

### Audit a Local Repository with PDF

```bash
python -m src.main \
  --repo ./local_repo \
  --report ./report.pdf

```

---

## StateGraph Flow

The architecture implements:

- Detective fan-out (parallel execution from `START`)
- EvidenceAggregator fan-in (state merge)
- Judge fan-out (parallel `Prosecutor`, `Defense`, `TechLead` from `EvidenceAggregator`)
- ChiefJustice fan-in and deterministic synthesis (final markdown + `AuditReport`)

See the PDF report for the full diagram (Figure 1).

---

## Reproducibility

This repository includes a `requirements.txt` file generated via `pip freeze` to ensure deterministic dependency installation.

To recreate the environment exactly:

```bash
pip install -r requirements.txt

```

---

## Design Principles

- **Typed State Management** – Pydantic models enforce structured data flow.
- **Deterministic Merging** – Explicit reducers prevent race-condition overwrites.
- **Sandboxed Execution** – Git operations run in isolated temporary directories.
- **AST-Based Code Analysis** – Structural parsing preferred over regex for robustness.
- **Extensible Judicial Layer** – Designed for persona-driven LLM evaluation with rule-based synthesis.

---

## Current Status

- Detective layer: Implemented
- Evidence aggregation: Implemented
- Vision integration: Stubbed (ready for multimodal API)
- Judicial layer: Planned
- ChiefJustice synthesis engine: Planned

---

## License

For academic and evaluation purposes.