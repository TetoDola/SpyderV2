"""Document ingestion service: PDF/DOCX/TXT/MD → clean plain text."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Regex to translate Obsidian [[Link]] to @[Link] (bracket-delimited)
# Negative lookbehind skips image embeds ![[image.jpg]]
OBSIDIAN_LINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
IMAGE_EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")


class DocumentExtractionError(Exception):
    """Raised when document text extraction fails."""


def extract_text(file_path: str | Path) -> str:
    """Extract plain text from a document file.

    Supported formats: .pdf, .docx, .txt, .md

    Args:
        file_path: Path to the document on disk.

    Returns:
        Clean plain text content.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise DocumentExtractionError(f"Document not found: {file_path}")

    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return _extract_pdf(file_path)
    elif suffix == ".docx":
        return _extract_docx(file_path)
    elif suffix == ".md":
        return _extract_markdown(file_path)
    elif suffix == ".txt":
        return _extract_txt(file_path)
    else:
        raise DocumentExtractionError(f"Unsupported file type: {suffix}")


def _extract_pdf(path: Path) -> str:
    """Extract text from PDF using pdfplumber."""
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)

    return "\n\n".join(pages).strip()


def _extract_docx(path: Path) -> str:
    """Extract text from DOCX using python-docx."""
    import docx

    doc = docx.Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs).strip()


def _extract_markdown(path: Path) -> str:
    """Read markdown, translate [[Link]] to @[Link], strip image embeds."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Remove image embeds
    text = IMAGE_EMBED_RE.sub("", raw)
    # Translate [[Link]] → @[Link]
    text = OBSIDIAN_LINK_RE.sub(r"@[\1]", text)
    return text.strip()


def _extract_txt(path: Path) -> str:
    """Read plain text file."""
    return path.read_text(encoding="utf-8", errors="replace").strip()
