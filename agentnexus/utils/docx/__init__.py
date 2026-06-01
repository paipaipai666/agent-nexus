"""DOCX 文档排版约束分析与操作工具。

提供三层能力：
- analyzer: 分析文档排版约束（零依赖，用 stdlib 解析 OpenXML）
- ops: 文档操作函数（依赖 python-docx）
- enforcer: 格式校验器

典型用法：
    from agentnexus.utils.docx import analyzer, ops

    # 分析约束
    print(analyzer.analyze_to_string("report.docx"))

    # 编辑文档
    ops.replace_text("report.docx", "旧文本", "新文本")
    ops.edit_table_cell("report.docx", 0, 1, 2, "新数据")
    ops.insert_image("report.docx", 5, "chart.png")
"""

from . import analyzer, enforcer, ops
from .constraints import (
    ColumnConstraints,
    DocumentConstraints,
    ImageConstraints,
    PageConstraints,
    SectionConstraints,
    StyleConstraints,
    TableConstraints,
)
from .enforcer import ValidationIssue

__all__ = [
    "analyzer",
    "ops",
    "enforcer",
    "ColumnConstraints",
    "DocumentConstraints",
    "ImageConstraints",
    "PageConstraints",
    "SectionConstraints",
    "StyleConstraints",
    "TableConstraints",
    "ValidationIssue",
]
