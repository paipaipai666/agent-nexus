from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from agentnexus.rag.models import SourceDocument

from .common import (
    _build_sectioned_document,
    _build_single_section_document,
    _compose_indexed_text,
    _normalize_common,
    clean_text,
)

_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_XLSX_MAIN_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_XLSX_REL_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


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

    # Extract tables
    for tbl in root.iterfind(".//w:body/w:tbl", _DOCX_NS):
        rows = []
        for tr in tbl.iterfind("w:tr", _DOCX_NS):
            cells = []
            for tc in tr.iterfind("w:tc", _DOCX_NS):
                cell_text = ' '.join(p.text or '' for p in tc.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"))
                cells.append(cell_text.strip())
            rows.append(' | '.join(cells))
        if rows:
            paragraphs.append(('\n'.join(rows), None))

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
