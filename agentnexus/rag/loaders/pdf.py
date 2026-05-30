from __future__ import annotations

try:
    import fitz
except ImportError:
    raise ImportError(
        "pymupdf is required for PDF loading. Install it with: pip install agentnexus[rag]"
    )

from agentnexus.rag.models import DocumentSection, SourceDocument

from .common import _compose_indexed_text, clean_text

_DEFAULT_PDF_OCR_LANGUAGE = "chi_sim+eng"


def _load_pdf(file_path: str) -> SourceDocument:
    pdf = fitz.open(file_path)
    try:
        page_payloads = [_extract_pdf_page_payload(page) for page in pdf]
    finally:
        pdf.close()

    page_texts = [payload["text"] for payload in page_payloads]
    raw_text = "\n\n".join(text for text in page_texts if text.strip()).strip()
    metadata = {
        "format": "pdf",
        "page_count": len(page_texts),
        "ocr_fallback_used": any(payload["used_ocr"] for payload in page_payloads),
    }
    indexed_sections: list[str] = []
    document = SourceDocument.create(
        source_uri=file_path,
        raw_text=raw_text,
        metadata=metadata,
        indexed_text="",
        sparse_text="",
        sections=[],
    )
    sections: list[DocumentSection] = []
    for index, payload in enumerate(page_payloads):
        page_number = index + 1
        page_text = payload["text"]
        cleaned_text = clean_text(page_text)
        page_metadata = {
            "format": "pdf",
            "page_number": page_number,
            "section_title": f"Page {page_number}",
            "ocr_fallback_used": payload["used_ocr"],
        }
        indexed_text = _compose_indexed_text(page_metadata, cleaned_text)
        indexed_sections.append(indexed_text)
        sections.append(
            DocumentSection.create(
                document.document_version,
                section_index=index,
                raw_text=page_text.strip(),
                indexed_text=indexed_text,
                sparse_text=indexed_text,
                metadata=page_metadata,
                page_number=page_number,
            )
        )
    document.sections = sections
    joined = "\n\n".join(section for section in indexed_sections if section).strip()
    document.indexed_text = joined
    document.sparse_text = joined
    return document


def _extract_pdf_page_payload(page) -> dict[str, object]:
    direct_text = page.get_text("text")
    if direct_text.strip():
        return {"text": direct_text, "used_ocr": False}
    ocr_text = _extract_pdf_page_text_with_ocr(page)
    return {"text": ocr_text or direct_text, "used_ocr": bool(ocr_text.strip())}


def _extract_pdf_page_text_with_ocr(page, language: str = _DEFAULT_PDF_OCR_LANGUAGE) -> str:
    get_textpage_ocr = getattr(page, "get_textpage_ocr", None)
    if callable(get_textpage_ocr):
        try:
            textpage = get_textpage_ocr(language=language, full=True)
            ocr_text = page.get_text("text", textpage=textpage)
            if ocr_text and ocr_text.strip():
                return ocr_text
        except Exception:
            pass

    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return ""

    try:
        pix = page.get_pixmap(dpi=200)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return pytesseract.image_to_string(image, lang=language).strip()
    except Exception:
        return ""
