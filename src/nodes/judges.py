from __future__ import annotations

import logging
from typing import List

from src.state import JudicialOpinion


logger = logging.getLogger(__name__)


class JudgeLLMClient:
    """Stub client representing a structured-output LLM for judges.

    This class is intentionally minimal and does not call any external services.
    It only exists to demonstrate how `.with_structured_output()` could be wired
    to the `JudicialOpinion` schema.
    """

    def with_structured_output(self, model: type[JudicialOpinion]) -> "JudgeLLMClient":
        """Return a client configured to emit `JudicialOpinion` objects.

        In a real implementation, this would configure the underlying LLM
        library to produce structured outputs validated against the Pydantic
        schema.
        """
        logger.debug("Configured judge LLM with structured output model: %s", model)
        return self

    def invoke(self, prompt: str) -> JudicialOpinion:
        """Return a dummy `JudicialOpinion` instance for demonstration only."""
        logger.debug("Invoking judge LLM stub with prompt: %s", prompt)
        return JudicialOpinion(
            judge="TechLead",
            criterion_id="demo",
            score=3,
            argument="Stub opinion; replace with real LLM call.",
            cited_evidence=[],
        )


def solicit_judge_opinions(prompt: str) -> List[JudicialOpinion]:
    """Example function showing how judges would query an LLM with structured output."""
    client = JudgeLLMClient()
    structured_client = client.with_structured_output(JudicialOpinion)
    opinion = structured_client.invoke(prompt)
    return [opinion]

