"""AgentNexus CLI"""
import typer
from rich.console import Console

app = typer.Typer(name="nexus", help="AgentNexus — 单智能体任务协同 CLI")
console = Console()

kb_app = typer.Typer(help="知识库管理")
app.add_typer(kb_app, name="kb")

memory_app = typer.Typer(help="记忆管理")
app.add_typer(memory_app, name="memory")

logs_app = typer.Typer(help="历史 Trace 查看")
app.add_typer(logs_app, name="logs")

eval_app = typer.Typer(help="RAG 评估")
app.add_typer(eval_app, name="eval")

from agentnexus.cli import kb         # noqa: E402
from agentnexus.cli import memory_cmd # noqa: E402
from agentnexus.cli import logs       # noqa: E402
from agentnexus.cli import eval_cmd   # noqa: E402
from agentnexus.cli import stats      # noqa: E402
from agentnexus.cli import config     # noqa: E402
from agentnexus.cli import audit      # noqa: E402
from agentnexus.cli import tui_cmd    # noqa: E402


@app.command()
def version():
    """显示版本"""
    console.print("[bold]AgentNexus[/bold] v0.1.0")
