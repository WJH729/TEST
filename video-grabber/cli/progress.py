from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

progress = Progress(
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    "[progress.percentage]{task.percentage:>3.0f}%",
    DownloadColumn(),
    TransferSpeedColumn(),
    TimeRemainingColumn(),
)


def make_progress() -> Progress:
    return progress


def progress_hook(d):
    """yt-dlp progress hook compatible callback."""
    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
        downloaded = d.get("downloaded_bytes", 0)
        for task in progress.tasks:
            if task.id == d.get("info_dict", {}).get("id"):
                progress.update(task.id, completed=downloaded, total=total)
                break
