"""DOCX 排版约束数据模型。

定义页面、表格、图片、样式等排版约束的数据结构，
供 analyzer 提取、ops 使用、enforcer 校验。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PageConstraints:
    """页面尺寸与边距约束。"""
    width_mm: float = 210.0         # 页面宽度（A4 默认）
    height_mm: float = 297.0        # 页面高度
    margin_top_mm: float = 25.4     # 上边距
    margin_bottom_mm: float = 25.4  # 下边距
    margin_left_mm: float = 31.8    # 左边距
    margin_right_mm: float = 31.8   # 右边距
    orientation: str = "portrait"   # "portrait" | "landscape"

    @property
    def usable_width_mm(self) -> float:
        """可用内容宽度（毫米）。"""
        return self.width_mm - self.margin_left_mm - self.margin_right_mm

    @property
    def usable_height_mm(self) -> float:
        """可用内容高度（毫米）。"""
        return self.height_mm - self.margin_top_mm - self.margin_bottom_mm

    @property
    def usable_width_chars(self) -> int:
        """可用宽度约等于多少个中文字符（12pt 宋体 ≈ 4.23mm/字）。"""
        return int(self.usable_width_mm / 4.23)

    @property
    def usable_height_lines(self) -> int:
        """可用高度约等于多少行（1.5 倍行距 12pt ≈ 6.35mm/行）。"""
        return int(self.usable_height_mm / 6.35)


@dataclass
class ColumnConstraints:
    """单列约束。"""
    index: int                      # 列索引（0-based）
    width_mm: float                 # 列宽（毫米）
    width_emu: int = 0              # 列宽（EMU，Office 内部单位）
    max_chars_cn: int = 0           # 最大中文字符数
    max_chars_en: int = 0           # 最大英文字符数

    def __post_init__(self):
        if not self.max_chars_cn and self.width_mm > 0:
            # 约 4.23mm per 中文字符（12pt 宋体）
            self.max_chars_cn = max(1, int(self.width_mm / 4.23))
        if not self.max_chars_en and self.width_mm > 0:
            # 约 2.12mm per 英文字符（12pt Times New Roman）
            self.max_chars_en = max(1, int(self.width_mm / 2.12))


@dataclass
class TableConstraints:
    """表格约束。"""
    table_index: int                # 表格在文档中的索引（0-based）
    total_width_mm: float           # 表格总宽度
    total_width_emu: int = 0        # 表格总宽度（EMU）
    columns: list[ColumnConstraints] = field(default_factory=list)
    row_count: int = 0              # 行数
    col_count: int = 0              # 列数
    has_header: bool = False        # 是否有表头行
    is_single_line_cell: bool = True  # 是否单行单元格
    location_desc: str = ""         # 位置描述（如"第3页"、"标题'一、项目概况'之后"）

    @property
    def column_widths_mm(self) -> list[float]:
        return [c.width_mm for c in self.columns]

    @property
    def max_chars_per_col(self) -> list[int]:
        return [c.max_chars_cn for c in self.columns]


@dataclass
class ImageConstraints:
    """图片约束。"""
    image_index: int                # 图片在文档中的索引（0-based）
    width_mm: float                 # 当前宽度
    height_mm: float                # 当前高度
    max_width_mm: float             # 最大允许宽度（= 可用宽度）
    max_height_mm: float            # 建议最大高度（超过会产生留白）
    location_desc: str = ""         # 位置描述
    is_oversized: bool = False      # 是否过大（超出页面）
    causes_whitespace: bool = False # 是否会导致大量留白


@dataclass
class StyleConstraints:
    """样式约束。"""
    name: str                       # 样式名称（如 "Heading1", "Normal"）
    display_name: str = ""          # 显示名称（如 "标题 1", "正文"）
    font_name: str = ""             # 字体名称
    font_size_pt: float = 0.0       # 字号（磅）
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str = ""                 # 颜色值（如 "#000000"）
    alignment: str = ""             # "left" | "center" | "right" | "justify"
    indent_first_mm: float = 0.0    # 首行缩进（毫米）
    indent_first_chars: float = 0.0 # 首行缩进（字符数）
    line_spacing: float = 0.0       # 行距倍数
    space_before_mm: float = 0.0    # 段前间距
    space_after_mm: float = 0.0     # 段后间距
    is_heading: bool = False        # 是否标题样式
    heading_level: int = 0          # 标题级别（1-9）

    @property
    def chars_per_line(self) -> int:
        """此样式下每行约能放多少中文字符。"""
        if self.font_size_pt <= 0:
            return 0
        # 字号磅值转毫米: 1pt = 0.3528mm
        char_width_mm = self.font_size_pt * 0.3528 * 1.0  # 中文字符约等宽
        if char_width_mm <= 0:
            return 0
        # 需要 PageConstraints 才能精确计算，这里返回近似值
        return max(1, int(146.4 / char_width_mm))  # 默认 A4 可用宽度


@dataclass
class SectionConstraints:
    """节（Section）约束——一个文档可以有多个节，每节有不同的页面设置。"""
    section_index: int
    page: PageConstraints = field(default_factory=PageConstraints)
    header_text: str = ""           # 页眉内容
    footer_text: str = ""           # 页脚内容
    page_number_start: int = -1     # 起始页码（-1 = 继续上一节）


@dataclass
class DocumentConstraints:
    """文档的完整排版约束。"""
    file_path: str
    file_size_bytes: int = 0
    page_count: int = 0             # 估算页数
    sections: list[SectionConstraints] = field(default_factory=list)
    tables: list[TableConstraints] = field(default_factory=list)
    images: list[ImageConstraints] = field(default_factory=list)
    styles: dict[str, StyleConstraints] = field(default_factory=dict)

    @property
    def default_page(self) -> PageConstraints:
        """获取默认（第一个节）的页面约束。"""
        if self.sections:
            return self.sections[0].page
        return PageConstraints()

    def summary(self) -> str:
        """生成 LLM 可读的约束摘要。"""
        lines: list[str] = []
        lines.append("═══ 文档排版约束 ═══")
        lines.append("")

        # 页面设置
        page = self.default_page
        lines.append("📄 页面设置")
        orientation_str = "横向" if page.orientation == "landscape" else "纵向"
        lines.append(f"  纸张: {page.width_mm:.0f}mm × {page.height_mm:.0f}mm ({orientation_str})")
        lines.append(f"  上边距: {page.margin_top_mm:.1f}mm  下边距: {page.margin_bottom_mm:.1f}mm")
        lines.append(f"  左边距: {page.margin_left_mm:.1f}mm  右边距: {page.margin_right_mm:.1f}mm")
        lines.append(f"  可用宽度: {page.usable_width_mm:.1f}mm (约 {page.usable_width_chars} 个中文字符)")
        lines.append(f"  可用高度: {page.usable_height_mm:.1f}mm (约 {page.usable_height_lines} 行)")
        lines.append("")

        # 样式定义
        if self.styles:
            lines.append("📐 样式定义")
            for name, style in self.styles.items():
                parts = []
                if style.font_name:
                    parts.append(f"字体={style.font_name}")
                if style.font_size_pt:
                    parts.append(f"字号={style.font_size_pt}pt")
                if style.bold:
                    parts.append("加粗")
                if style.italic:
                    parts.append("斜体")
                if style.alignment:
                    parts.append(f"对齐={style.alignment}")
                if style.indent_first_chars:
                    parts.append(f"首行缩进={style.indent_first_chars:.0f}字符")
                if style.line_spacing:
                    parts.append(f"行距={style.line_spacing}倍")
                display = style.display_name or name
                lines.append(f"  {display}: {' | '.join(parts)}")
            lines.append("")

        # 表格约束
        if self.tables:
            lines.append("📊 表格约束")
            for tbl in self.tables:
                lines.append(f"  表格 {tbl.table_index + 1}"
                             f" ({tbl.row_count}行×{tbl.col_count}列"
                             f", 总宽={tbl.total_width_mm:.1f}mm)"
                             f" {tbl.location_desc}")
                for col in tbl.columns:
                    lines.append(f"    列{col.index + 1}: {col.width_mm:.1f}mm"
                                 f" (约{col.max_chars_cn}个中文字符)")
                if tbl.is_single_line_cell:
                    lines.append("    ⚠️ 单行单元格：超出列宽会自动换行，导致行高变化")
            lines.append("")

        # 图片约束
        if self.images:
            lines.append("🖼️ 图片约束")
            for img in self.images:
                status = ""
                if img.is_oversized:
                    status = " ⚠️ 超出页面宽度"
                elif img.causes_whitespace:
                    status = " ⚠️ 会导致大量留白"
                lines.append(f"  图片 {img.image_index + 1}: "
                             f"{img.width_mm:.1f}mm × {img.height_mm:.1f}mm"
                             f" {img.location_desc}{status}")
            lines.append("")

        # 通用规则
        lines.append("⚠️ 关键规则")
        lines.append(f"  1. 表格内容不能超过列宽限制（{page.usable_width_chars}字符/行）")
        lines.append(f"  2. 图片宽度不能超过可用宽度 {page.usable_width_mm:.1f}mm")
        lines.append(f"  3. 图片高度超过 {page.usable_height_mm * 0.8:.0f}mm 会导致页面留白")
        lines.append("  4. 插入内容前先检查目标位置的约束")
        lines.append("  5. 编辑前务必备份原文件")

        return "\n".join(lines)
