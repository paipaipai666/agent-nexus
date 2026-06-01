"""DOCX 文档操作函数。

提供安全的文档编辑操作，内置约束检查。
每个函数返回结构化的操作结果字典。

使用 python-docx 作为底层实现（可选依赖）。
"""

from __future__ import annotations

import copy
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 延迟导入 python-docx，允许在未安装时仍能使用 analyzer
_docx = None


def _ensure_docx():
    """确保 python-docx 可用，否则抛出友好错误。"""
    global _docx
    if _docx is None:
        try:
            import docx
            _docx = docx
        except ImportError:
            raise ImportError(
                "python-docx 未安装。请运行: pip install python-docx\n"
                "或安装完整 RAG 依赖: pip install agentnexus[rag]"
            )
    return _docx


def _load_doc(path: str):
    """加载 DOCX 文档并返回 Document 对象。"""
    docx = _ensure_docx()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not p.is_file():
        raise ValueError(f"路径不是文件: {path}")
    return docx.Document(str(p))


def _ok(message: str, **extra: Any) -> dict[str, Any]:
    """构建成功结果。"""
    return {"status": "ok", "message": message, **extra}


def _err(message: str, **extra: Any) -> dict[str, Any]:
    """构建错误结果。"""
    return {"status": "error", "message": message, **extra}


# ── 读取操作 ──

def read_paragraphs(path: str) -> list[dict]:
    """读取所有段落及其格式属性。

    Returns:
        列表，每个元素包含:
        - index: 段落索引
        - text: 段落文本
        - style: 样式名称
        - font_name: 字体名称
        - font_size_pt: 字号（磅）
        - bold: 是否加粗
        - italic: 是否斜体
        - alignment: 对齐方式
        - line_spacing: 行距倍数
        - indent_first_char: 首行缩进字符数
    """
    doc = _load_doc(path)
    results = []
    for i, para in enumerate(doc.paragraphs):
        entry: dict[str, Any] = {
            "index": i,
            "text": para.text,
            "style": para.style.name if para.style else "",
            "font_name": "",
            "font_size_pt": 0.0,
            "bold": False,
            "italic": False,
            "alignment": "",
            "line_spacing": 0.0,
            "indent_first_char": 0.0,
        }

        # 样式级别的格式
        if para.style and para.style.font:
            font = para.style.font
            if font.name:
                entry["font_name"] = font.name
            if font.size:
                entry["font_size_pt"] = font.size.pt
            if font.bold:
                entry["bold"] = True
            if font.italic:
                entry["italic"] = True

        # 段落级别的格式
        pf = para.paragraph_format
        if pf.alignment is not None:
            align_map = {
                0: "left", 1: "center", 2: "right", 3: "justify",
            }
            entry["alignment"] = align_map.get(pf.alignment, str(pf.alignment))
        if pf.line_spacing:
            entry["line_spacing"] = float(pf.line_spacing)
        if pf.first_line_indent:
            # 将 EMU 转换为字符数（粗略：1字符 ≈ 字号宽度）
            indent_emu = pf.first_line_indent
            font_size = entry["font_size_pt"] or 12.0
            char_width_emu = font_size * 12700  # 1pt ≈ 12700 EMU
            if char_width_emu > 0:
                entry["indent_first_char"] = round(indent_emu / char_width_emu, 1)

        # 段落内第一个 run 的字符格式覆盖样式
        if para.runs:
            run = para.runs[0]
            if run.font.name:
                entry["font_name"] = run.font.name
            if run.font.size:
                entry["font_size_pt"] = run.font.size.pt
            if run.font.bold is not None:
                entry["bold"] = run.font.bold
            if run.font.italic is not None:
                entry["italic"] = run.font.italic

        results.append(entry)
    return results


def read_tables(path: str) -> list[dict]:
    """读取所有表格的结构和内容。

    Returns:
        列表，每个元素包含:
        - index: 表格索引
        - rows: 行数
        - cols: 列数
        - col_widths_mm: 各列宽度（毫米）
        - has_header: 是否有表头
        - cells: 二维数组，单元格内容
    """
    doc = _load_doc(path)
    results = []
    for i, table in enumerate(doc.tables):
        rows = len(table.rows)
        cols = len(table.columns)

        # 列宽
        col_widths = []
        for col in table.columns:
            width_emu = col.width if col.width else 0
            col_widths.append(round(width_emu / 36000, 1))  # EMU → mm

        # 单元格内容
        cells = []
        for row in table.rows:
            row_cells = []
            for cell in row.cells:
                row_cells.append(cell.text.strip())
            cells.append(row_cells)

        results.append({
            "index": i,
            "rows": rows,
            "cols": cols,
            "col_widths_mm": col_widths,
            "has_header": rows > 0,  # 简化：第一行视为表头
            "cells": cells,
        })
    return results


# ── 文本编辑 ──

def replace_text(
    path: str,
    old_text: str,
    new_text: str,
    style_preserve: bool = True,
    output_path: str | None = None,
) -> dict[str, Any]:
    """替换文档中的文本，保留原有格式。

    Args:
        path: DOCX 文件路径
        old_text: 要替换的原文本
        new_text: 新文本
        style_preserve: 是否保留原有格式（默认 True）
        output_path: 输出路径（None = 覆盖原文件）

    Returns:
        操作结果字典
    """
    if not old_text:
        return _err("old_text 不能为空")

    doc = _load_doc(path)
    count = 0

    for para in doc.paragraphs:
        if old_text in para.text:
            # python-docx 的段落由多个 run 组成，需要在 run 级别替换
            # 简单情况：文本在单个 run 中
            for run in para.runs:
                if old_text in run.text:
                    run.text = run.text.replace(old_text, new_text)
                    count += 1
            # 复杂情况：文本跨多个 run，需要重建
            if old_text in para.text and count == 0:
                full_text = para.text
                if old_text in full_text:
                    new_full = full_text.replace(old_text, new_text)
                    # 清空所有 run，将新文本写入第一个 run
                    if para.runs:
                        para.runs[0].text = new_full
                        for run in para.runs[1:]:
                            run.text = ""
                        count += 1

    # 也检查表格中的文本
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    if old_text in para.text:
                        for run in para.runs:
                            if old_text in run.text:
                                run.text = run.text.replace(old_text, new_text)
                                count += 1

    out = output_path or path
    doc.save(out)
    return _ok(
        f"已替换 {count} 处文本",
        replacements=count,
        output_path=out,
    )


def edit_table_cell(
    path: str,
    table_index: int,
    row: int,
    col: int,
    text: str,
    output_path: str | None = None,
) -> dict[str, Any]:
    """编辑指定表格单元格的内容。

    自动检查列宽约束，如果文本超出列宽会发出警告。

    Args:
        path: DOCX 文件路径
        table_index: 表格索引（0-based）
        row: 行索引（0-based）
        col: 列索引（0-based）
        text: 新文本
        output_path: 输出路径（None = 覆盖原文件）

    Returns:
        操作结果字典，包含约束警告（如果有）
    """
    doc = _load_doc(path)
    warnings: list[str] = []

    if table_index >= len(doc.tables):
        return _err(f"表格索引 {table_index} 超出范围（共 {len(doc.tables)} 个表格）")

    table = doc.tables[table_index]
    if row >= len(table.rows):
        return _err(f"行索引 {row} 超出范围（共 {len(table.rows)} 行）")
    if col >= len(table.columns):
        return _err(f"列索引 {col} 超出范围（共 {len(table.columns)} 列）")

    cell = table.rows[row].cells[col]

    # 检查列宽约束
    col_width_emu = table.columns[col].width if table.columns[col].width else 0
    if col_width_emu > 0:
        col_width_mm = col_width_emu / 36000
        # 估算最大中文字符数（12pt ≈ 4.23mm/字）
        max_chars = max(1, int(col_width_mm / 4.23))
        if len(text) > max_chars:
            warnings.append(
                f"文本长度 ({len(text)} 字符) 超出列宽限制 (约 {max_chars} 字符)。"
                f"超出部分会自动换行，可能导致行高变化。"
            )

    # 保留原有格式：清空现有段落，写入新文本
    # 保留第一个段落的格式
    if cell.paragraphs:
        first_para = cell.paragraphs[0]
        # 保留格式，替换文本
        if first_para.runs:
            first_para.runs[0].text = text
            for run in first_para.runs[1:]:
                run.text = ""
        else:
            first_para.text = text
        # 删除多余段落
        for para in cell.paragraphs[1:]:
            p_element = para._element
            p_element.getparent().remove(p_element)
    else:
        cell.text = text

    out = output_path or path
    doc.save(out)

    result = _ok(
        f"已更新表格 {table_index + 1} 第 {row + 1} 行第 {col + 1} 列",
        output_path=out,
    )
    if warnings:
        result["warnings"] = warnings
    return result


# ── 内容插入 ──

def insert_paragraph(
    path: str,
    after_index: int,
    text: str,
    style: str = "Normal",
    output_path: str | None = None,
) -> dict[str, Any]:
    """在指定段落之后插入新段落。

    Args:
        path: DOCX 文件路径
        after_index: 在此段落索引之后插入（0-based，-1 = 文档开头）
        text: 段落文本
        style: 样式名称
        output_path: 输出路径

    Returns:
        操作结果字典
    """
    doc = _load_doc(path)

    if after_index < -1 or after_index >= len(doc.paragraphs):
        return _err(
            f"段落索引 {after_index} 超出范围"
            f"（有效范围: -1 到 {len(doc.paragraphs) - 1}）"
        )

    # 在指定位置插入段落
    if after_index == -1:
        # 插入到文档开头
        ref_para = doc.paragraphs[0]
        new_para = doc.add_paragraph(text, style=style)
        # 移动到开头
        ref_para._element.addprevious(new_para._element)
    else:
        ref_para = doc.paragraphs[after_index]
        new_para = doc.add_paragraph(text, style=style)
        ref_para._element.addnext(new_para._element)

    out = output_path or path
    doc.save(out)
    return _ok(
        f"已在段落 {after_index} 之后插入新段落 (样式={style})",
        output_path=out,
        inserted_index=after_index + 1,
    )


def insert_image(
    path: str,
    after_index: int,
    image_path: str,
    width_mm: float | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """在指定位置插入图片。

    自动检查宽度约束：如果未指定宽度，默认撑满可用宽度。
    如果指定宽度超出可用宽度，自动缩放到可用宽度。

    Args:
        path: DOCX 文件路径
        after_index: 在此段落索引之后插入
        image_path: 图片文件路径
        width_mm: 图片宽度（毫米），None = 自动撑满可用宽度
        output_path: 输出路径

    Returns:
        操作结果字典
    """
    from .analyzer import analyze

    img_p = Path(image_path)
    if not img_p.exists():
        return _err(f"图片文件不存在: {image_path}")

    doc = _load_doc(path)

    if after_index < -1 or after_index >= len(doc.paragraphs):
        return _err(
            f"段落索引 {after_index} 超出范围"
            f"（有效范围: -1 到 {len(doc.paragraphs) - 1}）"
        )

    # 获取页面约束
    try:
        constraints = analyze(path)
        page = constraints.default_page
        max_width_mm = page.usable_width_mm
        max_height_mm = page.usable_height_mm * 0.8
    except Exception:
        max_width_mm = 146.4  # A4 默认可用宽度
        max_height_mm = 200.0

    warnings: list[str] = []

    # 确定图片宽度
    if width_mm is None:
        width_mm = max_width_mm
        warnings.append(f"未指定宽度，自动使用可用宽度 {max_width_mm:.1f}mm")
    elif width_mm > max_width_mm:
        warnings.append(
            f"指定宽度 {width_mm:.1f}mm 超出可用宽度 {max_width_mm:.1f}mm，已自动缩放"
        )
        width_mm = max_width_mm

    # 检查图片高度（粗略估算：保持宽高比）
    from docx.shared import Mm
    run_width = Mm(int(width_mm))

    # 插入图片
    if after_index == -1:
        ref_para = doc.paragraphs[0]
        new_para = doc.add_paragraph()
        run = new_para.add_run()
        run.add_picture(str(img_p), width=run_width)
        ref_para._element.addprevious(new_para._element)
    else:
        ref_para = doc.paragraphs[after_index]
        new_para = doc.add_paragraph()
        run = new_para.add_run()
        run.add_picture(str(img_p), width=run_width)
        ref_para._element.addnext(new_para._element)

    out = output_path or path
    doc.save(out)

    result = _ok(
        f"已在段落 {after_index} 之后插入图片 (宽度={width_mm:.1f}mm)",
        output_path=out,
        width_mm=width_mm,
    )
    if warnings:
        result["warnings"] = warnings
    return result


def insert_table(
    path: str,
    after_index: int,
    data: list[list[str]],
    col_widths_mm: list[float] | None = None,
    has_header: bool = True,
    output_path: str | None = None,
) -> dict[str, Any]:
    """在指定位置插入表格。

    如果未指定列宽，自动按可用宽度平均分配。

    Args:
        path: DOCX 文件路径
        after_index: 在此段落索引之后插入
        data: 表格数据（二维数组）
        col_widths_mm: 各列宽度（毫米），None = 平均分配
        has_header: 是否将第一行作为表头
        output_path: 输出路径

    Returns:
        操作结果字典
    """
    from .analyzer import analyze

    if not data or not data[0]:
        return _err("表格数据不能为空")

    rows = len(data)
    cols = len(data[0])

    doc = _load_doc(path)

    if after_index < -1 or after_index >= len(doc.paragraphs):
        return _err(
            f"段落索引 {after_index} 超出范围"
            f"（有效范围: -1 到 {len(doc.paragraphs) - 1}）"
        )

    # 获取页面约束，确定列宽
    try:
        constraints = analyze(path)
        page = constraints.default_page
        usable_width_mm = page.usable_width_mm
    except Exception:
        usable_width_mm = 146.4

    if col_widths_mm is None:
        col_widths_mm = [usable_width_mm / cols] * cols

    # 检查总列宽
    total_width = sum(col_widths_mm)
    warnings: list[str] = []
    if total_width > usable_width_mm * 1.05:  # 5% 容差
        warnings.append(
            f"表格总宽度 {total_width:.1f}mm 超出可用宽度 {usable_width_mm:.1f}mm"
        )

    # 创建表格
    from docx.shared import Mm
    table = doc.add_table(rows=rows, cols=cols)

    # 设置列宽
    for i, width in enumerate(col_widths_mm):
        for row in table.rows:
            row.cells[i].width = Mm(int(width))

    # 填充数据
    for r, row_data in enumerate(data):
        for c, cell_text in enumerate(row_data):
            if c < cols:
                table.rows[r].cells[c].text = cell_text

    # 表头样式
    if has_header and rows > 0:
        for cell in table.rows[0].cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True

    # 移动到指定位置
    if after_index >= 0 and after_index < len(doc.paragraphs):
        ref_para = doc.paragraphs[after_index]
        ref_para._element.addnext(table._element)
    elif after_index == -1 and doc.paragraphs:
        ref_para = doc.paragraphs[0]
        ref_para._element.addprevious(table._element)

    out = output_path or path
    doc.save(out)

    result = _ok(
        f"已插入 {rows}×{cols} 表格",
        output_path=out,
        rows=rows,
        cols=cols,
        col_widths_mm=col_widths_mm,
    )
    if warnings:
        result["warnings"] = warnings
    return result


# ── 页面设置 ──

def set_page_margins(
    path: str,
    top_mm: float | None = None,
    bottom_mm: float | None = None,
    left_mm: float | None = None,
    right_mm: float | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """设置页面边距（毫米）。

    Args:
        path: DOCX 文件路径
        top_mm: 上边距（None = 不修改）
        bottom_mm: 下边距
        left_mm: 左边距
        right_mm: 右边距
        output_path: 输出路径

    Returns:
        操作结果字典
    """
    from docx.shared import Mm

    doc = _load_doc(path)

    for section in doc.sections:
        if top_mm is not None:
            section.top_margin = Mm(int(top_mm))
        if bottom_mm is not None:
            section.bottom_margin = Mm(int(bottom_mm))
        if left_mm is not None:
            section.left_margin = Mm(int(left_mm))
        if right_mm is not None:
            section.right_margin = Mm(int(right_mm))

    out = output_path or path
    doc.save(out)
    return _ok(
        f"已设置页面边距: 上={top_mm}mm 下={bottom_mm}mm 左={left_mm}mm 右={right_mm}mm",
        output_path=out,
    )


# ── 文件操作 ──

def save_as(path: str, output_path: str) -> dict[str, Any]:
    """将文档另存为新文件（不修改原文件）。

    Args:
        path: 源文件路径
        output_path: 输出路径

    Returns:
        操作结果字典
    """
    p = Path(path)
    if not p.exists():
        return _err(f"源文件不存在: {path}")

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(p), str(out_p))

    return _ok(
        f"已另存为: {output_path}",
        source_path=path,
        output_path=output_path,
    )


def apply_template(
    content_path: str,
    template_path: str,
    output_path: str,
) -> dict[str, Any]:
    """将内容文档的文本应用到模板文档，继承模板的样式定义。

    工作流程：
    1. 读取模板文档的样式定义
    2. 读取内容文档的文本
    3. 在模板基础上创建新内容

    Args:
        content_path: 内容文档路径
        template_path: 模板文档路径
        output_path: 输出路径

    Returns:
        操作结果字典
    """
    content_doc = _load_doc(content_path)
    template_doc = _load_doc(template_path)

    # 提取内容文档的所有段落文本
    content_paragraphs = []
    for para in content_doc.paragraphs:
        text = para.text.strip()
        if text:
            content_paragraphs.append({
                "text": text,
                "style": para.style.name if para.style else "Normal",
            })

    # 清空模板文档的内容（保留样式定义）
    # python-docx 没有直接清空的方法，我们删除所有段落
    for para in template_doc.paragraphs:
        p_element = para._element
        p_element.getparent().remove(p_element)

    # 从模板文档的 body 中重新添加段落
    body = template_doc.element.body
    for item in content_paragraphs:
        new_para = template_doc.add_paragraph(item["text"], style=item["style"])
        body.append(new_para._element)

    template_doc.save(output_path)
    return _ok(
        f"已将 {len(content_paragraphs)} 个段落应用到模板",
        output_path=output_path,
        paragraphs_count=len(content_paragraphs),
    )


# ── 格式校验 ──

def validate(path: str) -> list[dict[str, str]]:
    """检查文档是否存在格式问题。

    Returns:
        问题列表，每个元素包含:
        - severity: "error" | "warning" | "info"
        - category: 问题类别
        - location: 位置描述
        - description: 问题描述
        - suggestion: 修复建议
    """
    from .enforcer import validate_document
    issues = validate_document(path)
    return [
        {
            "severity": issue.severity,
            "category": issue.category,
            "location": issue.location,
            "description": issue.description,
            "suggestion": issue.suggestion,
        }
        for issue in issues
    ]
