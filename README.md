# Automation Auditor

A modular, multi-agent architecture for auditing software repositories and accompanying PDF reports. The system orchestrates parallel "Detective" agents, aggregates structured evidence, and prepares for a judicial synthesis layer.

---

## Architecture Overview

The system is built around a partial StateGraph architecture with explicit fan-out and fan-in synchronization points.

### Implemented Components

- **Detectives (Parallel Fan-Out)**
  - `RepoInvestigator` – Repository structure, git history, and sandboxed cloning analysis.
  - `DocAnalyst` – PDF ingestion and chunked querying (RAG-lite approach).
  - `VisionInspector` – Diagram extraction and multimodal inspection (stubbed vision integration).
- **EvidenceAggregator (Fan-In)**
  - Collects and merges structured `Evidence` objects using typed reducers.

### Planned Components

- **Judges (Parallel Fan-Out)**
  - `Prosecutor`, `Defense`, `TechLead`
  - Structured output via `JudicialOpinion` schema.
- **ChiefJustice (Final Fan-In + Deterministic Synthesis)**
  - Applies rule-based overrides (Security Override, Fact Supremacy, Functionality Weight).
  - Produces final Markdown audit report.

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
- `graph.py` – Partial StateGraph wiring with Detective fan-out and EvidenceAggregator fan-in.
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
3. Evidence is aggregated using typed reducers.
4. (Planned) Judges evaluate structured evidence.
5. (Planned) ChiefJustice synthesizes final report.

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

- Detective fan-out (parallel execution)
- EvidenceAggregator fan-in (state merge)
- Planned judicial fan-out
- Planned deterministic synthesis fan-in

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