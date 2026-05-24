from __future__ import annotations

import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz

from .models import DocumentSection, SourceDocument

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".md", ".txt", ".html", ".htm", ".json", ".docx", ".xlsx"})
_FULLWIDTH_TABLE = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_XLSX_MAIN_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_XLSX_REL_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_DEFAULT_PDF_OCR_LANGUAGE = "chi_sim+eng"


def clean_text(text: str) -> str:
    text = _normalize_common(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    stripped_lines = [line.strip() for line in text.split("\n")]
    filtered_lines: list[str] = []
    for line in stripped_lines:
        if line:
            if len(line) >= 3 or re.search(r"[，。！？、；：\"\"''（）]", line):
                filtered_lines.append(line)
            continue
        if filtered_lines and filtered_lines[-1] != "":
            filtered_lines.append("")
    text = "\n".join(filtered_lines)
    text = re.sub(r"(?<=[^\x00-\x7f])\n(?=[^\x00-\x7f])", "", text)
    return text.strip()


def load_document(file_path: str) -> str:
    return load_structured_document(file_path).raw_text


def load_structured_document(file_path: str) -> SourceDocument:
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext}，支持: {SUPPORTED_EXTENSIONS}")
    if ext == ".pdf":
        return _load_pdf(file_path)
    if ext == ".md":
        return _load_markdown(file_path)
    if ext in {".html", ".htm"}:
        return _load_html(file_path)
    if ext == ".json":
        return _load_json(file_path)
    if ext == ".docx":
        return _load_docx(file_path)
    if ext == ".xlsx":
        return _load_xlsx(file_path)
    return _load_text(file_path)


def _normalize_common(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalized)
    return normalized.translate(_FULLWIDTH_TABLE)


def _clean_markdown_text(text: str) -> str:
    normalized = _normalize_common(text)
    lines = normalized.split("\n")
    cleaned_lines: list[str] = []
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if _is_code_fence(stripped):
            cleaned_lines.append(stripped)
            in_code_block = not in_code_block
            continue
        if in_code_block:
            cleaned_lines.append(line.rstrip())
        else:
            cleaned_lines.append(stripped)
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _load_text(file_path: str) -> SourceDocument:
    raw_text = Path(file_path).read_text(encoding="utf-8")
    indexed_text = clean_text(raw_text)
    return _build_single_section_document(
        file_path,
        raw_text=raw_text,
        indexed_text=indexed_text,
        file_format="text",
    )


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


def _load_html(file_path: str) -> SourceDocument:
    parser = _StructuredHtmlParser(Path(file_path).stem)
    parser.feed(Path(file_path).read_text(encoding="utf-8"))
    parser.close()
    parsed_sections = parser.build_sections()
    if not parsed_sections:
        return _build_single_section_document(
            file_path,
            raw_text="",
            indexed_text="",
            file_format="html",
        )
    return _build_sectioned_document(file_path, parsed_sections, {"format": "html"})


def _load_json(file_path: str) -> SourceDocument:
    data = json.loads(Path(file_path).read_text(encoding="utf-8"))
    parsed_sections = _split_json_sections(data, Path(file_path).stem)
    if not parsed_sections:
        rendered = clean_text(json.dumps(data, ensure_ascii=False, indent=2))
        return _build_single_section_document(
            file_path,
            raw_text=rendered,
            indexed_text=rendered,
            file_format="json",
        )
    return _build_sectioned_document(file_path, parsed_sections, {"format": "json"})


def _load_docx(file_path: str) -> SourceDocument:
    paragraphs = _extract_docx_paragraphs(file_path)
    parsed_sections = _split_docx_sections(paragraphs, Path(file_path).stem)
    if not parsed_sections:
        joined = clean_text("\n\n".join(text for text, _ in paragraphs if text.strip()))
        return _build_single_section_document(
            file_path,
            raw_text=joined,
            indexed_text=joined,
            file_format="docx",
        )
    return _build_sectioned_document(file_path, parsed_sections, {"format": "docx"})


def _load_xlsx(file_path: str) -> SourceDocument:
    sheets = _extract_xlsx_sheets(file_path)
    parsed_sections: list[tuple[str, str, dict]] = []
    for sheet_name, rows in sheets:
        raw_section = clean_text("\n".join(row for row in rows if row.strip()))
        if not raw_section:
            continue
        metadata = {"format": "xlsx", "section_title": sheet_name}
        parsed_sections.append((raw_section, _compose_indexed_text(metadata, raw_section), metadata))
    if not parsed_sections:
        return _build_single_section_document(
            file_path,
            raw_text="",
            indexed_text="",
            file_format="xlsx",
        )
    return _build_sectioned_document(file_path, parsed_sections, {"format": "xlsx"})


def _load_markdown(file_path: str) -> SourceDocument:
    raw_text = Path(file_path).read_text(encoding="utf-8")
    parsed_sections = _split_markdown_sections(raw_text, Path(file_path).stem)
    return _build_sectioned_document(file_path, parsed_sections, {"format": "markdown"}, raw_text_override=raw_text)


def _split_markdown_sections(raw_text: str, fallback_title: str) -> list[tuple[str, str, dict]]:
    normalized = _normalize_common(raw_text)
    lines = normalized.split("\n")
    sections: list[tuple[str, str, dict]] = []
    heading_path: list[str] = []
    buffer: list[str] = []
    in_code_block = False

    def flush_buffer():
        section_text = _clean_markdown_text("\n".join(buffer))
        buffer.clear()
        if not section_text:
            return
        metadata: dict[str, object] = {"format": "markdown"}
        if heading_path:
            metadata["heading_path"] = heading_path.copy()
            metadata["section_title"] = heading_path[-1]
        else:
            metadata["section_title"] = fallback_title
        indexed_text = _compose_indexed_text(metadata, section_text)
        sections.append((section_text, indexed_text, metadata))

    for line in lines:
        stripped = line.strip()
        if _is_code_fence(stripped):
            buffer.append(line)
            in_code_block = not in_code_block
            continue
        if not in_code_block:
            match = _HEADING_PATTERN.match(stripped)
            if match:
                flush_buffer()
                level = len(match.group(1))
                title = match.group(2).strip()
                heading_path[:] = heading_path[: level - 1]
                heading_path.append(title)
                continue
        buffer.append(line)

    flush_buffer()
    if sections:
        return sections

    fallback_text = _clean_markdown_text(raw_text)
    if not fallback_text:
        return []
    fallback_metadata = {
        "format": "markdown",
        "heading_path": [fallback_title],
        "section_title": fallback_title,
    }
    return [(fallback_text, _compose_indexed_text(fallback_metadata, fallback_text), fallback_metadata)]


def _compose_indexed_text(metadata: dict, body: str) -> str:
    heading_path = metadata.get("heading_path") or []
    prefix_parts = [part for part in heading_path if isinstance(part, str) and part]
    if not prefix_parts:
        section_title = metadata.get("section_title")
        if isinstance(section_title, str) and section_title:
            prefix_parts.append(section_title)
    prefix = "\n".join(prefix_parts)
    if prefix and body:
        return f"{prefix}\n\n{body}".strip()
    return (prefix or body).strip()


def _is_code_fence(line: str) -> bool:
    return line.startswith("```") or line.startswith("~~~")


def _build_single_section_document(
    file_path: str,
    *,
    raw_text: str,
    indexed_text: str,
    file_format: str,
) -> SourceDocument:
    document = SourceDocument.create(
        source_uri=file_path,
        raw_text=raw_text,
        metadata={"format": file_format},
        indexed_text=indexed_text,
        sparse_text=indexed_text,
        sections=[],
    )
    document.sections = [
        DocumentSection.create(
            document.document_version,
            section_index=0,
            raw_text=raw_text,
            indexed_text=indexed_text,
            sparse_text=indexed_text,
            metadata={"format": file_format, "section_title": Path(file_path).stem},
        )
    ]
    return document


def _build_sectioned_document(
    file_path: str,
    parsed_sections: list[tuple[str, str, dict]],
    metadata: dict,
    *,
    raw_text_override: str | None = None,
) -> SourceDocument:
    indexed_sections = [section[1] for section in parsed_sections if section[1]]
    joined_indexed = "\n\n".join(indexed_sections).strip()
    raw_text = raw_text_override
    if raw_text is None:
        raw_text = "\n\n".join(section[0] for section in parsed_sections if section[0]).strip()
    document = SourceDocument.create(
        source_uri=file_path,
        raw_text=raw_text,
        metadata=metadata,
        indexed_text=joined_indexed,
        sparse_text=joined_indexed,
        sections=[],
    )
    document.sections = [
        DocumentSection.create(
            document.document_version,
            section_index=index,
            raw_text=raw_section,
            indexed_text=indexed_text,
            sparse_text=indexed_text,
            metadata=section_metadata,
        )
        for index, (raw_section, indexed_text, section_metadata) in enumerate(parsed_sections)
    ]
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


def _split_json_sections(data, fallback_title: str) -> list[tuple[str, str, dict]]:
    sections: list[tuple[str, str, dict]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            section_text = _render_json_value(value, key)
            cleaned = clean_text(section_text)
            if not cleaned:
                continue
            metadata = {"format": "json", "section_title": str(key), "heading_path": [str(key)]}
            sections.append((cleaned, _compose_indexed_text(metadata, cleaned), metadata))
        return sections

    if isinstance(data, list):
        for index, value in enumerate(data):
            title = f"{fallback_title}[{index}]"
            section_text = _render_json_value(value, title)
            cleaned = clean_text(section_text)
            if not cleaned:
                continue
            metadata = {"format": "json", "section_title": title, "heading_path": [title]}
            sections.append((cleaned, _compose_indexed_text(metadata, cleaned), metadata))
        return sections

    rendered = clean_text(_render_json_scalar(data))
    if not rendered:
        return []
    metadata = {"format": "json", "section_title": fallback_title, "heading_path": [fallback_title]}
    return [(rendered, _compose_indexed_text(metadata, rendered), metadata)]


def _render_json_value(value, path: str) -> str:
    lines: list[str] = []

    def walk(node, current_path: str):
        if isinstance(node, dict):
            if not node:
                lines.append(f"{current_path}: {{}}")
                return
            for key, child in node.items():
                child_path = f"{current_path}.{key}" if current_path else str(key)
                walk(child, child_path)
            return
        if isinstance(node, list):
            if not node:
                lines.append(f"{current_path}: []")
                return
            for index, child in enumerate(node):
                walk(child, f"{current_path}[{index}]")
            return
        lines.append(f"{current_path}: {_render_json_scalar(node)}")

    walk(value, path)
    return "\n".join(lines)


def _render_json_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _extract_docx_paragraphs(file_path: str) -> list[tuple[str, str | None]]:
    with zipfile.ZipFile(file_path) as archive:
        document_xml = archive.read("word/document.xml")

    root = ET.fromstring(document_xml)
    paragraphs: list[tuple[str, str | None]] = []
    for paragraph in root.findall(".//w:body/w:p", _DOCX_NS):
        text_parts: list[str] = []
        for node in paragraph.iter():
            tag = _strip_xml_tag(node.tag)
            if tag == "t":
                text_parts.append(node.text or "")
            elif tag == "tab":
                text_parts.append("\t")
            elif tag in {"br", "cr"}:
                text_parts.append("\n")
        text = clean_text("".join(text_parts))
        style = paragraph.find("./w:pPr/w:pStyle", _DOCX_NS)
        style_name = style.attrib.get(f"{{{_DOCX_NS['w']}}}val") if style is not None else None
        if text:
            paragraphs.append((text, style_name))
    return paragraphs


def _split_docx_sections(
    paragraphs: list[tuple[str, str | None]],
    fallback_title: str,
) -> list[tuple[str, str, dict]]:
    sections: list[tuple[str, str, dict]] = []
    heading_path: list[str] = []
    buffer: list[str] = []

    def flush_buffer():
        body = clean_text("\n\n".join(buffer))
        buffer.clear()
        if not body:
            return
        metadata: dict[str, object] = {"format": "docx"}
        if heading_path:
            metadata["heading_path"] = heading_path.copy()
            metadata["section_title"] = heading_path[-1]
        else:
            metadata["section_title"] = fallback_title
        sections.append((body, _compose_indexed_text(metadata, body), metadata))

    for text, style_name in paragraphs:
        level = _docx_heading_level(style_name)
        if level is not None:
            flush_buffer()
            heading_path[:] = heading_path[: level - 1]
            heading_path.append(text)
            continue
        buffer.append(text)

    flush_buffer()
    return sections


def _docx_heading_level(style_name: str | None) -> int | None:
    if not style_name:
        return None
    normalized = style_name.casefold().replace(" ", "")
    match = re.search(r"heading(\d+)", normalized)
    if not match:
        return None
    return max(int(match.group(1)), 1)


def _extract_xlsx_sheets(file_path: str) -> list[tuple[str, list[str]]]:
    with zipfile.ZipFile(file_path) as archive:
        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        shared_strings = _load_xlsx_shared_strings(archive)

        rel_map = {
            rel.attrib.get("Id"): rel.attrib.get("Target", "")
            for rel in rels_root.findall("./rel:Relationship", _XLSX_REL_NS)
        }

        sheets: list[tuple[str, list[str]]] = []
        for sheet in workbook_root.findall(".//main:sheets/main:sheet", _XLSX_REL_NS):
            name = sheet.attrib.get("name", "Sheet")
            rel_id = sheet.attrib.get(f"{{{_XLSX_REL_NS['r']}}}id")
            target = rel_map.get(rel_id)
            if not target:
                continue
            sheet_path = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
            rows = _parse_xlsx_sheet(archive.read(sheet_path), shared_strings)
            sheets.append((name, rows))
        return sheets


def _load_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    strings: list[str] = []
    for item in root.findall("./main:si", _XLSX_MAIN_NS):
        text = "".join(node.text or "" for node in item.findall(".//main:t", _XLSX_MAIN_NS))
        strings.append(_normalize_common(text).strip())
    return strings


def _parse_xlsx_sheet(xml_bytes: bytes, shared_strings: list[str]) -> list[str]:
    root = ET.fromstring(xml_bytes)
    rows: list[str] = []
    for row in root.findall(".//main:sheetData/main:row", _XLSX_MAIN_NS):
        cells: list[str] = []
        for cell in row.findall("./main:c", _XLSX_MAIN_NS):
            value = _resolve_xlsx_cell_value(cell, shared_strings)
            if value:
                cells.append(value)
        if cells:
            rows.append("\t".join(cells))
    return rows


def _resolve_xlsx_cell_value(cell, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return _normalize_common("".join(node.text or "" for node in cell.findall(".//main:t", _XLSX_MAIN_NS))).strip()

    value_node = cell.find("./main:v", _XLSX_MAIN_NS)
    if value_node is None or value_node.text is None:
        return ""

    value = value_node.text.strip()
    if not value:
        return ""
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError):
            return value
    return value


def _strip_xml_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


class _StructuredHtmlParser(HTMLParser):
    _BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "header",
        "li",
        "main",
        "ol",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self, fallback_title: str):
        super().__init__(convert_charrefs=True)
        self._fallback_title = fallback_title
        self._ignore_depth = 0
        self._heading_level: int | None = None
        self._heading_parts: list[str] = []
        self._heading_path: list[str] = []
        self._buffer: list[str] = []
        self._sections: list[tuple[str, str, dict]] = []

    def handle_starttag(self, tag: str, attrs):
        del attrs
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript"}:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return
        if re.fullmatch(r"h[1-6]", normalized):
            self._flush_buffer()
            self._heading_level = int(normalized[1])
            self._heading_parts = []
            return
        if normalized in self._BLOCK_TAGS:
            self._buffer.append("\n")

    def handle_endtag(self, tag: str):
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript"}:
            self._ignore_depth = max(self._ignore_depth - 1, 0)
            return
        if self._ignore_depth:
            return
        if self._heading_level is not None and normalized == f"h{self._heading_level}":
            heading_text = clean_text("".join(self._heading_parts))
            if heading_text:
                self._heading_path[:] = self._heading_path[: self._heading_level - 1]
                self._heading_path.append(heading_text)
            self._heading_level = None
            self._heading_parts = []
            self._buffer.append("\n")
            return
        if normalized in self._BLOCK_TAGS:
            self._buffer.append("\n")

    def handle_data(self, data: str):
        if self._ignore_depth or not data.strip():
            return
        if self._heading_level is not None:
            self._heading_parts.append(data)
            return
        self._buffer.append(data)

    def build_sections(self) -> list[tuple[str, str, dict]]:
        self._flush_buffer()
        if self._sections:
            return self._sections
        fallback = clean_text("".join(self._buffer))
        if not fallback:
            return []
        metadata = {"format": "html", "section_title": self._fallback_title, "heading_path": [self._fallback_title]}
        return [(fallback, _compose_indexed_text(metadata, fallback), metadata)]

    def _flush_buffer(self):
        body = clean_text("".join(self._buffer))
        self._buffer.clear()
        if not body:
            return
        metadata: dict[str, object] = {"format": "html"}
        if self._heading_path:
            metadata["heading_path"] = self._heading_path.copy()
            metadata["section_title"] = self._heading_path[-1]
        else:
            metadata["section_title"] = self._fallback_title
        self._sections.append((body, _compose_indexed_text(metadata, body), metadata))
