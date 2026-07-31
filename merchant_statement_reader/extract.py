from __future__ import annotations

from pathlib import Path


class ExtractionError(RuntimeError):
    pass


def extract_statement_text(path: str | Path) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text(source)
    if suffix in {".txt", ".text"}:
        return source.read_text(encoding="utf-8", errors="replace")
    raise ExtractionError(f"Unsupported file type: {source.suffix}. Please upload a PDF or text file.")


def _extract_pdf_text(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise ExtractionError("PDF support needs pdfplumber installed.") from exc

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            pages.append(text)

    extracted = "\n".join(pages).strip()
    if not extracted:
        raise ExtractionError(
            "No selectable text was found in this PDF. It may be scanned and need OCR before parsing."
        )
    return extracted
