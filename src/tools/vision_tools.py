from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import List


logger = logging.getLogger(__name__)


def extract_images_from_pdf(path: str) -> List[Path]:
    """Extract images from a PDF and save them as temporary files.

    This helper uses PyMuPDF (``fitz``) when available to iterate over all pages
    and embedded images, writing each image as a separate PNG file into a
    temporary directory.

    Args:
        path: Filesystem path to the PDF file.

    Returns:
        A list of paths to the extracted image files. Returns an empty list when
        the PDF cannot be read, no images are found, or the required dependency
        is unavailable.
    """
    try:
        import fitz  # type: ignore[import]
    except ImportError as exc:
        logger.debug("PyMuPDF (fitz) is not installed: %s", exc)
        return []

    pdf_path = Path(path)
    if not pdf_path.is_file():
        logger.debug("PDF file not found for image extraction: %s", pdf_path)
        return []

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to open PDF %s with PyMuPDF: %s", pdf_path, exc)
        return []

    temp_dir = Path(tempfile.mkdtemp(prefix="audit_images_"))
    image_paths: List[Path] = []

    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            images = page.get_images(full=True)
            for img_index, img in enumerate(images):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    # Convert CMYK/other colorspaces to RGB if needed.
                    if pix.n > 4:
                        pix_converted = fitz.Pixmap(fitz.csRGB, pix)
                        pix = pix_converted
                    out_path = temp_dir / f"page{page_index}_img{img_index}.png"
                    pix.save(str(out_path))
                    image_paths.append(out_path)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "Failed to extract image xref=%s from %s page %s: %s",
                        xref,
                        pdf_path,
                        page_index,
                        exc,
                    )
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            # Closing failures are non-fatal and only logged at debug.
            logger.debug("Failed to close PDF document %s", pdf_path)

    return image_paths


def _call_vision_model(image_path: Path, prompt: str) -> str:
    """Call a multimodal vision model on the given image.

    Uses Google Gemini (free tier) when GEMINI_API_KEY or GOOGLE_API_KEY is set.
    Otherwise returns a stub message so the pipeline still produces evidence.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key or not api_key.strip():
        logger.debug(
            "No GEMINI_API_KEY or GOOGLE_API_KEY set; using vision stub for %s",
            image_path,
        )
        return (
            "Vision model stub: no real analysis performed. "
            "Set GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment to use "
            "Google Gemini free vision for diagram inspection."
        )

    try:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types  # type: ignore[import-untyped]
    except ImportError as exc:
        logger.warning(
            "google-genai not available for vision: %s. Using stub.",
            exc,
        )
        return (
            "Vision model stub: google-genai not installed. "
            "Install with: uv add google-genai Pillow"
        )

    try:
        client = genai.Client(api_key=api_key.strip())
        uploaded = client.files.upload(file=str(image_path))
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[prompt, uploaded],
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=1024,
            ),
        )
        if response.text:
            return response.text.strip()
        return (
            "Vision model returned no text (possibly safety filter). "
            "Diagram could not be classified."
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini vision call failed for %s: %s", image_path, exc)
        return (
            "Vision model stub: Gemini API call failed. "
            "Check your API key and network. Diagram was not analyzed."
        )


def analyze_flow(image_path: Path) -> str:
    """Analyze a diagram image for flow and diagram type using a vision model.

    The default implementation delegates to a stubbed vision model call,
    structured so that a real multimodal model can later be plugged in without
    changing callers.

    Args:
        image_path: Path to the diagram image file.

    Returns:
        A textual description of the diagram, including whether it resembles a
        StateGraph diagram or generic boxes, and whether the arrow flow follows
        ``Detectives (Parallel) -> Evidence Aggregation -> Judges (Parallel) -> Synthesis``.
    """
    prompt = (
        "You are analyzing an architectural diagram image for an Automation Auditor "
        "system.\n\n"
        "1. Classify the diagram type as one of: 'stategraph', 'sequence', "
        "'flowchart', or 'other'.\n"
        "2. Describe in 1-2 sentences the main flow of information.\n"
        "3. Specifically check whether there is a flow from an 'Evidence Aggregation' "
        "node to one or more judge roles labeled 'Prosecutor', 'Defense', or "
        "'TechLead', and then to a 'Chief Justice' or 'Synthesis' node. State clearly "
        "whether this flow is present.\n"
        "4. Also indicate whether the diagram looks like a StateGraph-style "
        "state machine or a generic box-and-arrow diagram.\n"
    )

    return _call_vision_model(image_path=image_path, prompt=prompt)

