# Minimal production image for the Automation Auditor.
# Run with: docker run --rm -e OPENAI_API_KEY -e REPO_URL -e PDF_PATH -v $(pwd):/workspace ...
FROM python:3.13-slim

# Git required for RepoInvestigator (sandboxed clone).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies (no dev); project code and rubric stay in /app.
COPY pyproject.toml uv.lock* ./
COPY src ./src/
COPY rubric.json ./

RUN pip install uv \
    && uv sync --no-dev --no-editable

ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

ENV PYTHONPATH=/app

# Write audit_report.md here; mount a volume to retrieve it.
WORKDIR /workspace

# Pass GROQ_API_KEY (required) and optionally GEMINI_API_KEY at run time.
# Override repo/report via env REPO_URL, PDF_PATH or by passing args after the image.
ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--repo", ".", "--report", ""]
