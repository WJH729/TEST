"""CLI interactive menu."""

import os
from pathlib import Path

import questionary
from rich import print
from rich.table import Table
from rich.panel import Panel

from ..grabber.ytdlp_wrapper import list_formats, get_metadata, download_url
from ..utils.formatter import format_size
from ..utils.history import load_history, add_record
from ..config import load_config, save_config
from .progress import progress, progress_hook


def main_menu():
    """Main loop."""
    cfg = load_config()
    Path(cfg["output_dir"]).mkdir(parents=True, exist_ok=True)

    while True:
        print()
        print(Panel("[bold cyan]网页视频抓取工具[/bold cyan]", width=60))
        choice = questionary.select(
            "请选择操作:",
            choices=[
                "下载视频",
                "批量下载",
                "下载历史",
                "设置",
                "退出",
            ],
        ).ask()

        if choice == "退出":
            print("[yellow]再见[/yellow]")
            break
        elif choice == "下载视频":
            handle_single_download(cfg)
        elif choice == "批量下载":
            handle_batch_download(cfg)
        elif choice == "下载历史":
            show_history()
        elif choice == "设置":
            handle_settings(cfg)


def handle_single_download(cfg: dict):
    """Single video download flow."""
    url = questionary.text("输入视频页面 URL:").ask()
    if not url:
        return

    print(f"\n[cyan]正在分析:[/cyan] {url}")
    meta = get_metadata(url, proxy=cfg.get("proxy", ""))
    if meta:
        print(f"[green]标题:[/green] {meta['title']}")
        print(f"[green]时长:[/green] {meta['duration']}")
        if meta.get("uploader"):
            print(f"[green]上传者:[/green] {meta['uploader']}")

    formats = list_formats(url, proxy=cfg.get("proxy", ""))
    if not formats:
        print("[red]未能获取视频信息，请检查 URL 或网络连接[/red]")
        return

    choices = []
    for i, f in enumerate(formats):
        label = f"{f['resolution']:>12} | {f['ext']:>4} | {f.get('note', ''):10} | {format_size(f['filesize']):>8}"
        choices.append(questionary.Choice(title=label, value=i))

    selected = questionary.checkbox(
        "选择要下载的视频格式 (空格选择，Enter 确认):",
        choices=choices,
    ).ask()

    if not selected:
        return

    output_dir = questionary.text("输出目录:", default=cfg["output_dir"]).ask()
    if not output_dir:
        return
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    format_ids = [formats[i]["format_id"] for i in selected]
    format_spec = "+".join(format_ids) if len(format_ids) > 1 else format_ids[0]

    with progress:
        task = progress.add_task(f"下载中...", total=None)
        ok = download_url(
            url, output_dir, format_spec=format_spec,
            proxy=cfg.get("proxy", ""),
            cookie_file=cfg.get("cookie_file", ""),
            progress_hooks=[progress_hook],
        )
        progress.remove_task(task)

    if ok:
        print(f"\n[green]✅ 下载完成![/green] 文件保存在: {output_dir}")
        add_record(url, meta.get("title", ""), output_dir, "成功")
    else:
        print(f"\n[red]❌ 下载失败[/red]")
        add_record(url, meta.get("title", ""), output_dir, "失败")

    questionary.press_any_key_to_continue("按 Enter 返回主菜单...").ask()


def handle_batch_download(cfg: dict):
    """Batch download from a file containing URLs (one per line)."""
    file_path = questionary.path("输入 URL 列表文件路径:").ask()
    if not file_path or not os.path.exists(file_path):
        print("[red]文件不存在[/red]")
        return

    with open(file_path, encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    print(f"[cyan]共发现 {len(urls)} 个链接[/cyan]")
    output_dir = questionary.text("输出目录:", default=cfg["output_dir"]).ask()
    if not output_dir:
        return
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    success = 0
    for url in urls:
        print(f"\n[cyan]正在下载:[/cyan] {url}")
        ok = download_url(
            url, output_dir, format_spec=cfg.get("default_format", "best"),
            proxy=cfg.get("proxy", ""),
            cookie_file=cfg.get("cookie_file", ""),
        )
        if ok:
            success += 1
            add_record(url, "", output_dir, "成功")
        else:
            add_record(url, "", output_dir, "失败")

    print(f"\n[green]批量下载完成: {success}/{len(urls)} 成功[/green]")
    questionary.press_any_key_to_continue("按 Enter 返回主菜单...").ask()


def show_history():
    """Display download history."""
    history = load_history()
    if not history:
        print("[yellow]暂无下载记录[/yellow]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    table = Table(title="下载历史")
    table.add_column("时间", style="cyan")
    table.add_column("标题", style="green")
    table.add_column("状态", style="bold")
    table.add_column("链接", style="blue", no_wrap=True)

    for record in reversed(history[-20:]):
        status_style = "[green]成功[/green]" if record["status"] == "成功" else "[red]失败[/red]"
        table.add_row(
            record.get("time", ""),
            (record.get("title", "") or "")[:30],
            status_style,
            (record.get("url", "") or "")[:50],
        )

    print(table)
    questionary.press_any_key_to_continue("按 Enter 返回...").ask()


def handle_settings(cfg: dict):
    """Settings menu."""
    while True:
        print()
        print(Panel("[bold]设置[/bold]", width=60))
        choice = questionary.select(
            "选择要修改的配置:",
            choices=[
                f"输出目录 ({cfg['output_dir']})",
                f"代理 ({cfg['proxy'] or '无'})",
                f"Cookie 文件 ({cfg['cookie_file'] or '无'})",
                f"并发数 ({cfg['concurrent']})",
                "返回主菜单",
            ],
        ).ask()

        if choice == "返回主菜单":
            save_config(cfg)
            break
        elif choice.startswith("输出目录"):
            val = questionary.text("输出目录:", default=cfg["output_dir"]).ask()
            if val:
                cfg["output_dir"] = val
        elif choice.startswith("代理"):
            val = questionary.text("代理地址 (留空清除):", default=cfg.get("proxy", "")).ask()
            cfg["proxy"] = val or ""
        elif choice.startswith("Cookie"):
            val = questionary.text("Cookie 文件路径 (留空清除):", default=cfg.get("cookie_file", "")).ask()
            cfg["cookie_file"] = val or ""
        elif choice.startswith("并发"):
            val = questionary.text("并发下载数:", default=str(cfg.get("concurrent", 3))).ask()
            if val and val.isdigit():
                cfg["concurrent"] = int(val)
