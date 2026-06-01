"""DOCX 格式校验器。

检查文档是否存在格式问题：表格溢出、图片超宽、留白等。
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass

from .analyzer import analyze

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """格式校验问题。"""
    severity: str        # "error" | "warning" | "info"
    category: str        # 问题类别
    location: str        # 位置描述
    description: str     # 问题描述
    suggestion: str      # 修复建议


def validate_document(path: str) -> list[ValidationIssue]:
    """检查文档是否存在格式问题。

    检查项：
    - 表格内容是否超出列宽
    - 图片是否超出页面宽度
    - 图片是否导致大量留白
    - 表格总宽度是否合适

    Args:
        path: DOCX 文件路径

    Returns:
        问题列表
    """
    issues: list[ValidationIssue] = []

    try:
        constraints = analyze(path)
    except FileNotFoundError:
        issues.append(ValidationIssue(
            severity="error",
            category="file_not_found",
            location=path,
            description=f"文件不存在: {path}",
            suggestion="检查文件路径是否正确",
        ))
        return issues
    except zipfile.BadZipFile:
        issues.append(ValidationIssue(
            severity="error",
            category="invalid_format",
            location=path,
            description=f"文件不是有效的 DOCX 格式: {path}",
            suggestion="确认文件是否为 .docx 格式",
        ))
        return issues

    page = constraints.default_page

    # 检查表格
    for tbl in constraints.tables:
        # 检查表格总宽度
        if tbl.total_width_mm > page.usable_width_mm * 1.05:
            issues.append(ValidationIssue(
                severity="warning",
                category="table_overflow",
                location=f"表格 {tbl.table_index + 1}",
                description=(
                    f"表格总宽度 ({tbl.total_width_mm:.1f}mm) 超出可用宽度"
                    f" ({page.usable_width_mm:.1f}mm)"
                ),
                suggestion="减小表格宽度或调整页面边距",
            ))

        # 检查每列的字符容量
        for col in tbl.columns:
            if col.width_mm > 0 and col.max_chars_cn < 3:
                issues.append(ValidationIssue(
                    severity="info",
                    category="column_narrow",
                    location=f"表格 {tbl.table_index + 1} 列 {col.index + 1}",
                    description=(
                        f"列宽仅 {col.width_mm:.1f}mm (约 {col.max_chars_cn} 个中文字符)，"
                        f"内容容易溢出"
                    ),
                    suggestion="增大列宽或减少单元格内容",
                ))

        # 检查固定布局的单行约束
        if tbl.is_single_line_cell:
            for col in tbl.columns:
                if col.max_chars_cn > 0:
                    issues.append(ValidationIssue(
                        severity="info",
                        category="single_line_cell",
                        location=f"表格 {tbl.table_index + 1} 列 {col.index + 1}",
                        description=(
                            f"固定布局列宽 {col.width_mm:.1f}mm，"
                            f"每行最多约 {col.max_chars_cn} 个中文字符。"
                            f"超出会自动换行导致行高变化。"
                        ),
                        suggestion=f"确保每列内容不超过 {col.max_chars_cn} 个字符",
                    ))

    # 检查图片
    for img in constraints.images:
        if img.is_oversized:
            issues.append(ValidationIssue(
                severity="warning",
                category="image_overflow",
                location=img.location_desc,
                description=(
                    f"图片宽度 ({img.width_mm:.1f}mm) 超出可用宽度"
                    f" ({page.usable_width_mm:.1f}mm)，会导致溢出或不对齐"
                ),
                suggestion=f"将图片宽度设置为 {page.usable_width_mm:.1f}mm 或更小",
            ))

        if img.causes_whitespace:
            issues.append(ValidationIssue(
                severity="warning",
                category="image_whitespace",
                location=img.location_desc,
                description=(
                    f"图片高度 ({img.height_mm:.1f}mm) 超过页面可用高度的 80%"
                    f" ({page.usable_height_mm * 0.8:.0f}mm)，"
                    f"会导致页面大量留白"
                ),
                suggestion=(
                    "建议：1) 缩小图片高度至页面高度的 80% 以内；"
                    "2) 将长图片拆分为多页；"
                    "3) 使用 '图片环绕' 布局减少留白"
                ),
            ))

    return issues
