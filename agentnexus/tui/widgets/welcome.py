"""An immediately usable welcome state for empty terminal conversations."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from agentnexus.tui.branding import LOGO


class Welcome(Static):
    """Project block logo and live workspace metadata on a terminal grid."""

    def __init__(self, model="未配置", workspace="", thinking="默认", recent="", **kwargs):
        super().__init__(**kwargs)
        self.model = model
        self.workspace = workspace
        self.thinking = thinking
        self.recent = recent

    def render(self):
        try:
            release = version("agentnexus")
        except PackageNotFoundError:
            release = "dev"
        workspace = Path(self.workspace).name if self.workspace else Path.cwd().name
        fields = {
            1: (f"AgentNexus  v{release}", "bold"),
            3: ("主代理 · 子代理 · 记忆与 Wiki", "dim"),
            5: (f"工作区  {workspace}", ""),
            6: (f"模型    {self.model}", ""),
            7: (f"思考    {self.thinking}", "dim"),
        }
        result = Text()
        accent = self.app.current_theme.primary or "magenta"
        narrow = self.size.width < 64
        for index, line in enumerate(LOGO):
            result.append(line, accent)
            if not narrow:
                text, style = fields.get(index, ("", ""))
                result.append("     ")
                result.append(text, style)
            result.append("\n")
        if narrow:
            result.append(f"AgentNexus  ·  {self.model}\n", "bold")
        if self.recent:
            result.append("\n继续上次任务\n", "dim")
            result.append("F2  ", accent)
            result.append(self.recent)
        else:
            result.append("\n输入任务，或按 Ctrl+P 查找操作", "dim")
        return result
