"""Progress bar helpers using rich."""

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)


def create_progress() -> Progress:
    """Create and return a new Progress instance for download tracking."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    )


class ProgressHook:
    """yt-dlp compatible progress hook that updates a rich Progress task."""

    def __init__(self, progress: Progress, task_id: int):
        self.progress = progress
        self.task_id = task_id

    def __call__(self, d: dict):
        if d["status"] == "downloading":
            total = (
                d.get("total_bytes")
                or d.get("total_bytes_estimate")
                or 0
            )
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                self.progress.update(
                    self.task_id, total=total, completed=downloaded
                )
                speed = d.get("speed") or 0
                if speed:
                    self.progress.update(self.task_id, speed=speed)
        elif d["status"] == "finished":
            task = self.progress.tasks[self.task_id]
            self.progress.update(
                self.task_id, total=task.total or 1, completed=task.total or 1
            )
