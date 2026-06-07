"""Optional local OCR helpers for absorb.

OCR is intentionally optional and local-only. Missing Python packages or system
tools return a clear status so parser callers can keep safe fallback behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def ocr_available() -> dict[str, Any]:
    missing: list[str] = []
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        missing.append("pytesseract")
    try:
        import PIL.Image  # noqa: F401
    except ImportError:
        missing.append("pillow")
    try:
        import pdf2image  # noqa: F401
    except ImportError:
        missing.append("pdf2image")
    return {"ok": not missing, "missing": missing}


def ocr_image(path: str | Path) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("missing_optional_dependency:absorb-ocr") from exc
    try:
        image = Image.open(Path(path))
        return str(pytesseract.image_to_string(image) or "")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"ocr_image_failed:{exc}") from exc


def ocr_pdf(path: str | Path, *, max_pages: int = 10) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise RuntimeError("missing_optional_dependency:absorb-ocr") from exc
    try:
        pages = convert_from_path(str(Path(path)), first_page=1, last_page=max_pages)
        texts = [str(pytesseract.image_to_string(page) or "") for page in pages]
        return "\n".join(text for text in texts if text.strip())
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"ocr_pdf_failed:{exc}") from exc
