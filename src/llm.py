"""
Centralized LLM factory for the Automation Auditor.

Uses Groq (free tier, no credit card required) as the default provider.
Configurable via GROQ_API_KEY and LLM_MODEL environment variables.

This module also defines a node -> model mapping so heavy nodes can use
larger models while lighter nodes use cheaper/faster ones.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

# Default: Groq's Llama 3.3 70B (free, strong general model).
_DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Per-node model preferences (can be overridden via env LLM_MODEL).
NODE_LLM_MODEL: Dict[str, str] = {
    # Detectives
    "RepoInvestigator": "llama-3.1-8b-instant",
    "DocAnalyst": "llama-3.1-8b-instant",
    # VisionInspector uses Gemini via vision_tools, not ChatGroq.

    # Judges (heavier reasoning, higher-impact)
    "Prosecutor": "llama-3.3-70b-versatile",
    "Defense": "llama-3.3-70b-versatile",
    "TechLead": "llama-3.3-70b-versatile",
}


def _resolve_model(node_name: Optional[str]) -> str:
    """
    Resolve which Groq model to use for a given node.

    Order of precedence:
    1. LLM_MODEL env var (global override)
    2. NODE_LLM_MODEL[node_name] if provided
    3. _DEFAULT_MODEL
    """
    # Global override wins.
    override = os.environ.get("LLM_MODEL")
    if override:
        return override

    if node_name and node_name in NODE_LLM_MODEL:
        return NODE_LLM_MODEL[node_name]

    return _DEFAULT_MODEL


def get_chat_llm(node_name: Optional[str] = None, temperature: float = 0):
    """
    Return a LangChain chat model for use in detectives and judges.

    Uses ChatGroq (Groq free tier) by default. Requires GROQ_API_KEY.

    Args:
        node_name: Optional logical node name (e.g., "Prosecutor",
            "RepoInvestigator") used to pick a per-node model.
        temperature: Sampling temperature for the model.
    """
    from langchain_groq import ChatGroq  # type: ignore[import]

    model = _resolve_model(node_name)
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY is required. Get a free key at https://console.groq.com"
        )
    return ChatGroq(model=model, temperature=temperature, api_key=api_key)
