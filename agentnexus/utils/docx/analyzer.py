"""DOCX 排版约束分析器。

使用 stdlib zipfile + xml.etree 解析 OpenXML 格式，
提取页面设置、样式定义、表格结构、图片引用等排版约束。

不依赖 python-docx，零外部依赖。
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .constraints import (
    ColumnConstraints,
    DocumentConstraints,
    ImageConstraints,
    PageConstraints,
    SectionConstraints,
    StyleConstraints,
    TableConstraints,
)

logger = logging.getLogger(__name__)

# ── OpenXML 命名空间 ──

_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"

_NS_MAP = {
    "w": _NS_W,
    "wp": _NS_WP,
    "a": _NS_A,
    "r": _NS_R,
    "rel": _NS_REL,
    "pic": _NS_PIC,
}


def _tag(ns: str, local: str) -> str:
    """构造带命名空间的 XML 标签名。"""
    return f"{{{_NS_MAP[ns]}}}{local}"


def _attr(el: ET.Element, ns: str, attr: str) -> str | None:
    """获取元素的带命名空间属性值。"""
    return el.attrib.get(_tag(ns, attr))


def _find(el: ET.Element, path: str) -> ET.Element | None:
    """查找子元素（使用命名空间前缀）。"""
    return el.find(path, _NS_MAP)


def _findall(el: ET.Element, path: str) -> list[ET.Element]:
    """查找所有匹配的子元素。"""
    return el.findall(path, _NS_MAP)


def _text(el: ET.Element | None) -> str:
    """获取元素的文本内容。"""
    if el is None:
        return ""
    return (el.text or "").strip()


# ── 单位转换 ──

def _twips_to_mm(twips: int | str) -> float:
    """将 twips 转换为毫米。1 inch = 1440 twips = 25.4mm。"""
    if isinstance(twips, str):
        try:
            twips = int(twips)
        except ValueError:
            return 0.0
    return twips * 25.4 / 1440


def _emu_to_mm(emu: int | str) -> float:
    """将 EMU 转换为毫米。1 inch = 914400 EMU = 25.4mm。"""
    if isinstance(emu, str):
        try:
            emu = int(emu)
        except ValueError:
            return 0.0
    return emu * 25.4 / 914400


def _pt_to_mm(pt: float | str) -> float:
    """将磅值转换为毫米。1 pt = 0.3528mm。"""
    if isinstance(pt, str):
        try:
            pt = float(pt)
        except ValueError:
            return 0.0
    return pt * 0.3528


def _half_pt_to_pt(half_pt: int | str) -> float:
    """将半磅值转换为磅值（OpenXML 中字号常用 half-point）。"""
    if isinstance(half_pt, str):
        try:
            half_pt = int(half_pt)
        except ValueError:
            return 0.0
    return half_pt / 2


# ── 页面约束分析 ──

def _parse_page_constraints(sect_pr: ET.Element) -> PageConstraints:
    """从 w:sectPr 元素解析页面约束。"""
    page = PageConstraints()

    pg_sz = _find(sect_pr, "w:pgSz")
    if pg_sz is not None:
        w = _attr(pg_sz, "w", "w")
        h = _attr(pg_sz, "w", "h")
        if w:
            page.width_mm = _twips_to_mm(w)
        if h:
            page.height_mm = _twips_to_mm(h)
        orient = _attr(pg_sz, "w", "orient")
        if orient:
            page.orientation = orient

    pg_mar = _find(sect_pr, "w:pgMar")
    if pg_mar is not None:
        for attr_name, field_name in [
            ("top", "margin_top_mm"),
            ("bottom", "margin_bottom_mm"),
            ("left", "margin_left_mm"),
            ("right", "margin_right_mm"),
        ]:
            val = _attr(pg_mar, "w", attr_name)
            if val:
                setattr(page, field_name, _twips_to_mm(val))

    return page


def _parse_section_constraints(
    sect_pr: ET.Element, index: int
) -> SectionConstraints:
    """解析单个节的约束。"""
    section = SectionConstraints(
        section_index=index,
        page=_parse_page_constraints(sect_pr),
    )

    # 页眉
    header_ref = _find(sect_pr, "w:headerReference")
    if header_ref is not None:
        section.header_text = _attr(header_ref, "w", "type") or ""

    # 页脚
    footer_ref = _find(sect_pr, "w:footerReference")
    if footer_ref is not None:
        section.footer_text = _attr(footer_ref, "w", "type") or ""

    # 起始页码
    pg_num_type = _find(sect_pr, "w:pgNumType")
    if pg_num_type is not None:
        start = _attr(pg_num_type, "w", "start")
        if start:
            try:
                section.page_number_start = int(start)
            except ValueError:
                pass

    return section


# ── 样式分析 ──

def _parse_style(style_el: ET.Element) -> StyleConstraints | None:
    """解析单个 w:style 元素。"""
    style_id = _attr(style_el, "w", "styleId")
    if not style_id:
        return None

    style_type = _attr(style_el, "w", "type") or ""
    name_el = _find(style_el, "w:name")
    display_name = _attr(name_el, "w", "val") if name_el is not None else ""

    style = StyleConstraints(
        name=style_id,
        display_name=display_name or style_id,
    )

    # 判断是否为标题
    if style_type == "paragraph" and display_name:
        import re
        heading_match = re.search(r"heading\s*(\d+)", display_name, re.IGNORECASE)
        if heading_match:
            style.is_heading = True
            style.heading_level = int(heading_match.group(1))

    # 段落属性
    ppr = _find(style_el, "w:pPr")
    if ppr is not None:
        # 对齐
        jc = _find(ppr, "w:jc")
        if jc is not None:
            val = _attr(jc, "w", "val")
            align_map = {
                "left": "left", "center": "center",
                "right": "right", "both": "justify",
                "justify": "justify",
            }
            style.alignment = align_map.get(val or "", "")

        # 缩进
        ind = _find(ppr, "w:ind")
        if ind is not None:
            first_line = _attr(ind, "w", "firstLine")
            first_line_chars = _attr(ind, "w", "firstLineChars")
            if first_line:
                style.indent_first_mm = _twips_to_mm(first_line)
            if first_line_chars:
                try:
                    style.indent_first_chars = int(first_line_chars) / 100
                except ValueError:
                    pass

        # 行距
        spacing = _find(ppr, "w:spacing")
        if spacing is not None:
            line = _attr(spacing, "w", "line")
            line_rule = _attr(spacing, "w", "lineRule")
            if line and line_rule == "auto":
                try:
                    style.line_spacing = int(line) / 240
                except ValueError:
                    pass
            before = _attr(spacing, "w", "before")
            if before:
                style.space_before_mm = _twips_to_mm(before)
            after = _attr(spacing, "w", "after")
            if after:
                style.space_after_mm = _twips_to_mm(after)

    # 字符属性
    rpr = _find(style_el, "w:rPr")
    if rpr is not None:
        _apply_run_properties(rpr, style)

    return style


def _apply_run_properties(rpr: ET.Element, style: StyleConstraints) -> None:
    """从 w:rPr 元素应用字符格式到 StyleConstraints。"""
    # 字体
    rfonts = _find(rpr, "w:rFonts")
    if rfonts is not None:
        # 优先 ascii，其次 eastAsia
        font = (_attr(rfonts, "w", "ascii")
                or _attr(rfonts, "w", "eastAsia")
                or _attr(rfonts, "w", "hAnsi"))
        if font:
            style.font_name = font

    # 字号
    sz = _find(rpr, "w:sz")
    if sz is not None:
        val = _attr(sz, "w", "val")
        if val:
            style.font_size_pt = _half_pt_to_pt(val)

    # 加粗
    b = _find(rpr, "w:b")
    if b is not None:
        val = _attr(b, "w", "val")
        style.bold = val != "0"

    # 斜体
    i = _find(rpr, "w:i")
    if i is not None:
        val = _attr(i, "w", "val")
        style.italic = val != "0"

    # 下划线
    u = _find(rpr, "w:u")
    if u is not None:
        val = _attr(u, "w", "val")
        style.underline = val not in (None, "none")

    # 颜色
    color = _find(rpr, "w:color")
    if color is not None:
        val = _attr(color, "w", "val")
        if val:
            style.color = f"#{val}"


def _parse_styles_xml(styles_bytes: bytes) -> dict[str, StyleConstraints]:
    """解析 word/styles.xml，返回所有样式定义。"""
    styles: dict[str, StyleConstraints] = {}
    try:
        root = ET.fromstring(styles_bytes)
    except ET.ParseError:
        return styles

    for style_el in _findall(root, "w:style"):
        style = _parse_style(style_el)
        if style:
            styles[style.name] = style

    return styles


# ── 表格分析 ──

def _parse_table(
    tbl_el: ET.Element,
    table_index: int,
    page: PageConstraints,
) -> TableConstraints:
    """解析 w:tbl 元素的表格约束。"""
    tbl = TableConstraints(
        table_index=table_index,
        total_width_mm=page.usable_width_mm,
        col_count=0,
        row_count=0,
    )

    # 表格属性
    tbl_pr = _find(tbl_el, "w:tblPr")
    if tbl_pr is not None:
        # 表格宽度
        tbl_w = _find(tbl_pr, "w:tblW")
        if tbl_w is not None:
            w_val = _attr(tbl_w, "w", "val")
            w_type = _attr(tbl_w, "w", "type")
            if w_val:
                if w_type == "pct":
                    # 百分比：5000 = 100%
                    try:
                        pct = int(w_val) / 5000
                        tbl.total_width_mm = page.usable_width_mm * pct
                    except ValueError:
                        pass
                elif w_type == "dxa":
                    tbl.total_width_mm = _twips_to_mm(w_val)
                    tbl.total_width_emu = int(w_val) * 914400 // 1440 if w_val.isdigit() else 0

        # 表格布局（fixed vs autofit）
        tbl_layout = _find(tbl_pr, "w:tblLayout")
        if tbl_layout is not None:
            layout_type = _attr(tbl_layout, "w", "type")
            # fixed = 固定列宽, autofit = 自动调整
            tbl.is_single_line_cell = layout_type == "fixed"

    # 列宽定义（w:tblGrid）
    tbl_grid = _find(tbl_el, "w:tblGrid")
    if tbl_grid is not None:
        grid_cols = _findall(tbl_grid, "w:gridCol")
        tbl.col_count = len(grid_cols)
        for i, col_el in enumerate(grid_cols):
            w_val = _attr(col_el, "w", "w")
            width_mm = _twips_to_mm(w_val) if w_val else 0.0
            tbl.columns.append(ColumnConstraints(
                index=i,
                width_mm=width_mm,
                width_emu=int(w_val) * 914400 // 1440 if w_val and w_val.isdigit() else 0,
            ))

    # 行数和表头
    rows = _findall(tbl_el, "w:tr")
    tbl.row_count = len(rows)
    if rows:
        # 检查第一行是否为表头（tblHeader 属性）
        first_row_pr = _find(rows[0], "w:trPr")
        if first_row_pr is not None:
            tbl_header = _find(first_row_pr, "w:tblHeader")
            tbl.has_header = tbl_header is not None

    # 如果没有从 tblGrid 获取到列宽，尝试从单元格推断
    if not tbl.columns and rows:
        first_row_cells = _findall(rows[0], "w:tc")
        tbl.col_count = len(first_row_cells)
        for i, tc_el in enumerate(first_row_cells):
            tc_pr = _find(tc_el, "w:tcPr")
            width_mm = 0.0
            if tc_pr is not None:
                tc_w = _find(tc_pr, "w:tcW")
                if tc_w is not None:
                    w_val = _attr(tc_w, "w", "val")
                    if w_val:
                        width_mm = _twips_to_mm(w_val)
            tbl.columns.append(ColumnConstraints(index=i, width_mm=width_mm))

    return tbl


# ── 图片分析 ──

def _parse_drawings(body: ET.Element, page: PageConstraints) -> list[ImageConstraints]:
    """解析文档中所有图片的约束。"""
    images: list[ImageConstraints] = []
    img_index = 0

    # 查找所有 w:drawing 元素
    for drawing in body.iter(_tag("w", "drawing")):
        # 查找 wp:inline 或 wp:anchor
        inline = drawing.find(f".//{_tag('wp', 'inline')}")
        anchor = drawing.find(f".//{_tag('wp', 'anchor')}")
        extent_parent = inline or anchor

        if extent_parent is None:
            continue

        extent = extent_parent.find(_tag("wp", "extent"))
        if extent is None:
            continue

        cx = _attr(extent, "wp", "cx")
        cy = _attr(extent, "wp", "cy")
        if not cx or not cy:
            continue

        width_mm = _emu_to_mm(cx)
        height_mm = _emu_to_mm(cy)

        max_width = page.usable_width_mm
        max_height = page.usable_height_mm * 0.8  # 80% 页面高度

        img = ImageConstraints(
            image_index=img_index,
            width_mm=width_mm,
            height_mm=height_mm,
            max_width_mm=max_width,
            max_height_mm=max_height,
            is_oversized=width_mm > max_width,
            causes_whitespace=height_mm > max_height,
        )

        # 简单位置描述
        img.location_desc = f"图片 {img_index + 1}"
        images.append(img)
        img_index += 1

    return images


# ── 段落分析（用于位置描述） ──

def _extract_paragraphs_metadata(body: ET.Element) -> list[dict]:
    """提取段落的元数据（样式、文本摘要），用于定位表格和图片的位置。"""
    paragraphs = []
    for para in body.findall("w:p", _NS_MAP):
        meta: dict = {"text": "", "style": ""}

        # 样式
        ppr = _find(para, "w:pPr")
        if ppr is not None:
            pstyle = _find(ppr, "w:pStyle")
            if pstyle is not None:
                meta["style"] = _attr(pstyle, "w", "val") or ""

        # 文本摘要
        text_parts = []
        for run in para.findall("w:r", _NS_MAP):
            for t in run.findall("w:t", _NS_MAP):
                if t.text:
                    text_parts.append(t.text)
        meta["text"] = "".join(text_parts)[:50]  # 只保留前50字符

        paragraphs.append(meta)
    return paragraphs


# ── 主分析函数 ──

def analyze(path: str) -> DocumentConstraints:
    """分析 DOCX 文件的完整排版约束。

    解析 OpenXML 提取：
    - 页面设置 (w:sectPr → w:pgSz, w:pgMar)
    - 样式定义 (word/styles.xml → w:style)
    - 表格结构 (w:tbl → w:tblGrid, w:tcPr → w:tcW)
    - 图片引用 (w:drawing → wp:extent)
    - 段落格式 (w:pPr → jc, ind, spacing)

    Args:
        path: DOCX 文件路径

    Returns:
        DocumentConstraints 包含完整的排版约束信息

    Raises:
        FileNotFoundError: 文件不存在
        zipfile.BadZipFile: 文件不是有效的 DOCX/ZIP 格式
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not p.is_file():
        raise ValueError(f"路径不是文件: {path}")

    constraints = DocumentConstraints(
        file_path=str(p.absolute()),
        file_size_bytes=p.stat().st_size,
    )

    with zipfile.ZipFile(str(p), "r") as archive:
        # 1. 解析主文档
        try:
            document_xml = archive.read("word/document.xml")
        except KeyError:
            logger.warning("DOCX 文件中未找到 word/document.xml")
            return constraints

        try:
            root = ET.fromstring(document_xml)
        except ET.ParseError as e:
            logger.warning(f"解析 document.xml 失败: {e}")
            return constraints

        body = root.find(f".//{_tag('w', 'body')}")
        if body is None:
            return constraints

        # 2. 解析节属性（页面设置）
        sections: list[SectionConstraints] = []
        # 文档级别的 sectPr（在 body 末尾）
        doc_sect_pr = _find(body, "w:sectPr")
        if doc_sect_pr is not None:
            sections.append(_parse_section_constraints(doc_sect_pr, 0))
        # 段落内的 sectPr（分节符）
        for i, para in enumerate(body.findall("w:p", _NS_MAP)):
            ppr = _find(para, "w:pPr")
            if ppr is not None:
                sect_pr = _find(ppr, "w:sectPr")
                if sect_pr is not None:
                    sections.append(_parse_section_constraints(sect_pr, len(sections)))

        constraints.sections = sections

        # 3. 解析样式定义
        try:
            styles_xml = archive.read("word/styles.xml")
            constraints.styles = _parse_styles_xml(styles_xml)
        except KeyError:
            logger.debug("DOCX 文件中未找到 word/styles.xml")

        # 4. 解析表格
        tables = body.findall("w:tbl", _NS_MAP)
        default_page = constraints.default_page
        for i, tbl_el in enumerate(tables):
            tbl = _parse_table(tbl_el, i, default_page)
            # 尝试用前面的段落来描述位置
            tbl.location_desc = f"表格 {i + 1}"
            constraints.tables.append(tbl)

        # 5. 解析图片
        constraints.images = _parse_drawings(body, default_page)

        # 6. 估算页数
        # 粗略估算：统计段落数 / 每页行数
        para_count = len(body.findall("w:p", _NS_MAP))
        lines_per_page = default_page.usable_height_lines
        if lines_per_page > 0:
            constraints.page_count = max(1, para_count // lines_per_page)

    return constraints


def analyze_to_string(path: str) -> str:
    """分析并返回 LLM 可读的约束摘要文本。

    这是 Skill 的主要入口——LLM 调用此函数获取文档的完整排版约束。

    Args:
        path: DOCX 文件路径

    Returns:
        格式化的约束摘要文本
    """
    try:
        constraints = analyze(path)
        return constraints.summary()
    except FileNotFoundError as e:
        return f"错误: {e}"
    except zipfile.BadZipFile:
        return f"错误: 文件不是有效的 DOCX 格式: {path}"
    except Exception as e:
        return f"错误: 分析文档时出错: {e}"


def read_paragraphs(path: str) -> list[dict]:
    """读取文档所有段落的元数据。

    返回列表，每个元素包含：
    - index: 段落索引
    - text: 段落文本
    - style: 样式名称
    - font: 字体名称
    - font_size: 字号（磅）
    - bold: 是否加粗
    - alignment: 对齐方式

    Args:
        path: DOCX 文件路径

    Returns:
        段落元数据列表
    """
    paragraphs: list[dict] = []

    with zipfile.ZipFile(path, "r") as archive:
        try:
            document_xml = archive.read("word/document.xml")
        except KeyError:
            return paragraphs

        root = ET.fromstring(document_xml)
        body = root.find(f".//{_tag('w', 'body')}")
        if body is None:
            return paragraphs

        # 预加载样式定义
        try:
            styles_xml = archive.read("word/styles.xml")
            styles = _parse_styles_xml(styles_xml)
        except KeyError:
            styles = {}

        for i, para in enumerate(body.findall("w:p", _NS_MAP)):
            meta: dict = {
                "index": i,
                "text": "",
                "style": "",
                "font": "",
                "font_size": 0.0,
                "bold": False,
                "alignment": "",
            }

            # 样式
            ppr = _find(para, "w:pPr")
            if ppr is not None:
                pstyle = _find(ppr, "w:pStyle")
                if pstyle is not None:
                    style_name = _attr(pstyle, "w", "val") or ""
                    meta["style"] = style_name
                    # 从样式定义继承属性
                    if style_name in styles:
                        s = styles[style_name]
                        meta["font"] = s.font_name
                        meta["font_size"] = s.font_size_pt
                        meta["bold"] = s.bold
                        meta["alignment"] = s.alignment

                jc = _find(ppr, "w:jc")
                if jc is not None:
                    val = _attr(jc, "w", "val")
                    align_map = {
                        "left": "left", "center": "center",
                        "right": "right", "both": "justify",
                    }
                    meta["alignment"] = align_map.get(val or "", meta["alignment"])

            # 文本和字符格式
            text_parts = []
            for run in para.findall("w:r", _NS_MAP):
                for t in run.findall("w:t", _NS_MAP):
                    if t.text:
                        text_parts.append(t.text)
                # 段落内第一个 run 的字符格式覆盖样式
                rpr = _find(run, "w:rPr")
                if rpr and not meta["font"]:
                    rfonts = _find(rpr, "w:rFonts")
                    if rfonts is not None:
                        meta["font"] = (_attr(rfonts, "w", "ascii")
                                        or _attr(rfonts, "w", "eastAsia") or "")
                    sz = _find(rpr, "w:sz")
                    if sz is not None:
                        val = _attr(sz, "w", "val")
                        if val:
                            meta["font_size"] = _half_pt_to_pt(val)
                    b = _find(rpr, "w:b")
                    if b is not None:
                        meta["bold"] = _attr(b, "w", "val") != "0"

            meta["text"] = "".join(text_parts)
            paragraphs.append(meta)

    return paragraphs
