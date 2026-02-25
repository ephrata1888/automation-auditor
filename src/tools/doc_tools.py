from __future__ import annotations

import logging
from pathlib import Path
from typing import List


logger = logging.getLogger(__name__)


def ingest_pdf(path: str) -> List[str]:
    """Parse a PDF file into a list of text chunks using Docling.

    The PDF is converted to markdown (or plain text when available) and then
    split into paragraph-like chunks for downstream keyword search.

    Args:
        path: Filesystem path to the PDF file.

    Returns:
        A list of text chunks. Returns an empty list if parsing fails or the
        file does not exist.
    """
    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import]
    except ImportError as exc:
        logger.debug("Docling is not installed: %s", exc)
        return []

    pdf_path = Path(path)
    if not pdf_path.is_file():
        logger.debug("PDF file not found: %s", pdf_path)
        return []

    try:
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to convert PDF %s with Docling: %s", pdf_path, exc)
        return []

    text_content: str
    try:
        # Prefer markdown export; fall back to plain text if available.
        document = getattr(result, "document", None) or result
        if hasattr(document, "export_to_markdown"):
            text_content = document.export_to_markdown()
        elif hasattr(document, "export_to_text"):
            text_content = document.export_to_text()
        else:
            logger.debug(
                "Docling result for %s does not support text export methods",
                pdf_path,
            )
            return []
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to extract text from Docling result for %s: %s", pdf_path, exc)
        return []

    # Split on double newlines to approximate paragraphs/sections.
    chunks = [chunk.strip() for chunk in text_content.split("\n\n") if chunk.strip()]
    return chunks


def find_keyword_chunks(chunks: List[str], keywords: List[str]) -> List[str]:
    """Return all chunks that contain any of the given keywords.

    Matching is done in a case-insensitive manner.

    Args:
        chunks: List of text chunks.
        keywords: Keywords to search for.

    Returns:
        A list of chunks that contain at least one of the keywords.
    """
    if not chunks or not keywords:
        return []

    lowered_keywords = [kw.lower() for kw in keywords if kw]
    if not lowered_keywords:
        return []

    matched: List[str] = []
    for chunk in chunks:
        lowered_chunk = chunk.lower()
        if any(kw in lowered_chunk for kw in lowered_keywords):
            matched.append(chunk)

    return matched


def extract_path_like_strings(chunks: List[str]) -> List[str]:
    """Extract path-like strings from PDF text chunks.

    This uses simple heuristics to find tokens that look like source file paths,
    such as ``src/nodes/judges.py`` or ``src\\nodes\\judges.py``.

    Args:
        chunks: List of text chunks from a PDF.

    Returns:
        A list of unique, normalized path-like strings.
    """
    candidates: List[str] = []

    for chunk in chunks:
        # Split on whitespace and common punctuation; keep anything that
        # contains a path separator and a file extension.
        for token in chunk.replace("(", " ").replace(")", " ").replace(",", " ").split():
            cleaned = token.strip(" .\"'")
            if not cleaned:
                continue
            if ("/" in cleaned or "\\" in cleaned) and "." in Path(cleaned).name:
                candidates.append(cleaned)

    # Normalize Windows-style backslashes to forward slashes and de-duplicate.
    normalized = {c.replace("\\", "/") for c in candidates}
    return sorted(normalized)


