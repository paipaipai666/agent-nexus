"""A compact activity indicator that animates only during real work."""

from rich.text import Text
from textual.widgets import Static

from agentnexus.tui.activity import Activity


class ActivityStatus(Static):
    """Keep lifecycle state visible independently of model configuration."""

    DEFAULT_CSS = """
    ActivityStatus { width: auto; height: 1; margin-right: 2; text-wrap: nowrap; text-overflow: ellipsis; color: $text-muted; }
    ActivityStatus.working { color: $primary; }
    ActivityStatus.waiting, ActivityStatus.confirming, ActivityStatus.unavailable { color: $warning; }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.activity = Activity()
        self.frame = 0

    def on_mount(self):
        self._timer = self.set_interval(0.25, self._tick, pause=True)
        self.update_activity(self.activity)

    def update_activity(self, activity):
        self.activity = activity
        self.set_classes(activity.mode)
        self.tooltip = activity.label()
        if self.is_mounted:
            self._timer.resume() if activity.mode == "working" else self._timer.pause()
        self.update(Text(activity.label(self.frame)))

    def _tick(self):
        self.frame = (self.frame + 1) % 4
        self.update(Text(self.activity.label(self.frame)))

    async def on_click(self):
        await self.screen.action_perform("tasks")
