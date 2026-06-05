"""CLI interactive menu."""

import os
from pathlib import Path

import questionary
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import load_config, save_config
from grabber.ytdlp_wrapper import (
    download_playlist,
    download_url,
    get_metadata,
    get_playlist_entries,
    list_formats,
)
from grabber.page_parser import parse_page, extract_page_metadata
from utils.formatter import format_size
from utils.history import add_record, clear_history, load_history
from cli.progress import ProgressHook, create_progress

console = Console()


def main_menu():
    """Main interactive loop."""
    cfg = load_config()
    Path(cfg["output_dir"]).mkdir(parents=True, exist_ok=True)

    while True:
        console.clear()
        print(Panel.fit(
            "[bold cyan]  网页视频抓取工具 v1.0  [/bold cyan]\n"
            f"[dim]  输出目录: {cfg['output_dir']}[/dim]",
            width=60,
        ))

        choice = questionary.select(
            "请选择操作:",
            choices=[
                questionary.Choice(title="🎬  下载视频", value="single"),
                questionary.Choice(title="🎵  提取音频", value="audio"),
                questionary.Choice(title="📋  播放列表下载", value="playlist"),
                questionary.Choice(title="📦  批量下载 (URL列表文件)", value="batch"),
                questionary.Choice(title="📜  下载历史", value="history"),
                questionary.Choice(title="⚙️  设置", value="settings"),
                questionary.Choice(title="❌  退出", value="quit"),
            ],
            qmark="",
        ).ask()

        if choice == "quit":
            print("[yellow]再见[/yellow]")
            break
        elif choice == "single":
            _handle_download(cfg, audio_only=False)
        elif choice == "audio":
            _handle_download(cfg, audio_only=True)
        elif choice == "playlist":
            _handle_playlist(cfg)
        elif choice == "batch":
            _handle_batch(cfg)
        elif choice == "history":
            _show_history()
        elif choice == "settings":
            _handle_settings(cfg)


def _handle_download(cfg: dict, audio_only: bool = False):
    """Single video / audio download flow."""
    console.clear()
    url = questionary.text(
        "输入视频页面 URL:" if not audio_only else "输入音频/视频页面 URL (仅提取音频):",
        validate=lambda t: True if t.strip() else "URL 不能为空",
    ).ask()
    if not url:
        return

    mode_label = "音频" if audio_only else "视频"
    print(f"\n[cyan]  正在分析 ({mode_label}模式)...[/cyan]")

    # Try yt-dlp first
    meta = get_metadata(url, proxy=cfg.get("proxy", ""))
    formats = list_formats(url, proxy=cfg.get("proxy", ""))

    # Fall back to page parser
    if not meta or not meta.get("title"):
        print("[dim]  yt-dlp 无结果，使用页面解析器...[/dim]")
        meta = extract_page_metadata(url)
    if not formats:
        print("[dim]  尝试通用页面解析...[/dim]")
        raw = parse_page(url)
        if raw:
            formats = [
                {
                    "format_id": r["src"],
                    "ext": r.get("ext", "mp4"),
                    "resolution": "未知",
                    "filesize": None,
                    "vcodec": "?",
                    "acodec": "?",
                    "fps": None,
                    "note": r["type"],
                    "tbr": None,
                }
                for r in raw
            ]

    if meta:
        _show_meta(meta)

    if not formats:
        print("[red]  未能检测到视频资源，请确认 URL 是否正确[/red]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    # Show format table
    _show_format_table(formats, audio_only)

    if audio_only:
        output_dir = questionary.text(
            "输出目录:", default=cfg["output_dir"]
        ).ask()
        if not output_dir:
            return
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        progress = create_progress()
        with progress:
            task_id = progress.add_task(
                f"提取音频: {meta.get('title', url)[:40]}", total=None
            )
            hook = ProgressHook(progress, task_id)
            ok = download_url(
                url, output_dir, audio_only=True,
                audio_format=cfg.get("audio_format", "mp3"),
                proxy=cfg.get("proxy", ""),
                cookie_file=cfg.get("cookie_file", ""),
                max_retries=cfg.get("max_retries", 3),
                ffmpeg_path=cfg.get("ffmpeg_path", ""),
                progress_hooks=[hook],
            )
    else:
        # Format selection
        choices = [
            questionary.Choice(
                title=f"{f['resolution']:>12}  {f['ext']:>5}  {format_size(f['filesize']):>10}  {f.get('note', ''):12}  [{f['format_id']}]",
                value=i,
            )
            for i, f in enumerate(formats)
        ]
        choices.insert(0, questionary.Choice(title="最佳质量 (自动选择)", value=-1))

        selected = questionary.select(
            "选择要下载的视频格式:",
            choices=choices,
            qmark="",
        ).ask()
        if selected is None:
            return

        output_dir = questionary.text(
            "输出目录:", default=cfg["output_dir"]
        ).ask()
        if not output_dir:
            return
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        if selected == -1:
            format_spec = cfg.get("default_format", "bestvideo+bestaudio/best")
        else:
            format_spec = formats[selected]["format_id"]

        progress = create_progress()
        with progress:
            task_id = progress.add_task(
                f"下载: {meta.get('title', url)[:40]}", total=None
            )
            hook = ProgressHook(progress, task_id)
            ok = download_url(
                url, output_dir, format_spec=format_spec,
                proxy=cfg.get("proxy", ""),
                cookie_file=cfg.get("cookie_file", ""),
                merge_format=cfg.get("merge_format", "mp4"),
                max_retries=cfg.get("max_retries", 3),
                continue_dl=cfg.get("continue_dl", True),
                embed_thumbnail=cfg.get("embed_thumbnail", True),
                write_metadata=cfg.get("write_metadata", True),
                ffmpeg_path=cfg.get("ffmpeg_path", ""),
                progress_hooks=[hook],
            )

    if ok:
        print(f"\n[green]  下载完成! → {output_dir}[/green]")
        add_record(url, meta.get("title", url), output_dir, "成功")
    else:
        print(f"\n[red]  下载失败，请检查网络或重试[/red]")
        add_record(url, meta.get("title", url), output_dir, "失败")

    questionary.press_any_key_to_continue("按 Enter 返回主菜单...").ask()


def _handle_playlist(cfg: dict):
    """Playlist download flow."""
    console.clear()
    url = questionary.text("输入播放列表 URL:", validate=lambda t: True if t.strip() else "不能为空").ask()
    if not url:
        return

    print(f"\n[cyan]  正在分析播放列表...[/cyan]")
    entries = get_playlist_entries(url, proxy=cfg.get("proxy", ""))
    if not entries:
        print("[red]  未能获取播放列表内容[/red]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    print(f"\n[green]  播放列表共 {len(entries)} 个视频:[/green]")
    for i, e in enumerate(entries[:10]):
        print(f"  {i+1}. {e['title'][:50]}  ({e['duration']})")
    if len(entries) > 10:
        print(f"  ... 还有 {len(entries) - 10} 个")

    confirm = questionary.confirm(
        f"\n确定下载全部 {len(entries)} 个视频？",
        default=False,
    ).ask()
    if not confirm:
        return

    output_dir = questionary.text("输出目录:", default=cfg["output_dir"]).ask()
    if not output_dir:
        return
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    print(f"\n[cyan]  开始下载播放列表...[/cyan]")
    progress = create_progress()
    with progress:
        task_id = progress.add_task("播放列表下载", total=len(entries))
        hook = ProgressHook(progress, task_id)

        success, total = download_playlist(
            url, output_dir,
            format_spec=cfg.get("default_format", "bestvideo+bestaudio/best"),
            proxy=cfg.get("proxy", ""),
            cookie_file=cfg.get("cookie_file", ""),
            merge_format=cfg.get("merge_format", "mp4"),
            max_retries=cfg.get("max_retries", 3),
            ffmpeg_path=cfg.get("ffmpeg_path", ""),
            progress_hooks=[hook],
        )

        progress.update(task_id, completed=total)

    print(f"\n[green]  播放列表下载完成: {success}/{total} 成功[/green]")
    add_record(url, f"播放列表 ({success}/{total})", output_dir, "成功" if success == total else "部分成功")
    questionary.press_any_key_to_continue("按 Enter 返回...").ask()


def _handle_batch(cfg: dict):
    """Batch download from a file."""
    console.clear()
    file_path = questionary.path("输入 URL 列表文件路径:").ask()
    if not file_path or not os.path.exists(file_path):
        print("[red]  文件不存在[/red]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    with open(file_path, encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    print(f"[cyan]  共发现 {len(urls)} 个链接[/cyan]")
    output_dir = questionary.text("输出目录:", default=cfg["output_dir"]).ask()
    if not output_dir:
        return
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    success = 0
    progress = create_progress()
    with progress:
        task_id = progress.add_task("批量下载", total=len(urls))
        for i, url in enumerate(urls):
            progress.update(task_id, description=f"批量下载 [{i+1}/{len(urls)}]")
            ok = download_url(
                url, output_dir,
                format_spec=cfg.get("default_format", "bestvideo+bestaudio/best"),
                proxy=cfg.get("proxy", ""),
                cookie_file=cfg.get("cookie_file", ""),
                merge_format=cfg.get("merge_format", "mp4"),
                max_retries=cfg.get("max_retries", 3),
                ffmpeg_path=cfg.get("ffmpeg_path", ""),
            )
            if ok:
                success += 1
                add_record(url, "", output_dir, "成功")
            else:
                add_record(url, "", output_dir, "失败")
            progress.advance(task_id)

    print(f"\n[green]  批量下载完成: {success}/{len(urls)} 成功[/green]")
    questionary.press_any_key_to_continue("按 Enter 返回...").ask()


def _show_history():
    """Display download history with management options."""
    console.clear()
    history = load_history()
    if not history:
        print("[yellow]  暂无下载记录[/yellow]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    table = Table(title="下载历史")
    table.add_column("#", style="dim")
    table.add_column("时间", style="cyan", width=20)
    table.add_column("标题", style="green", width=30)
    table.add_column("状态")
    table.add_column("链接", style="dim", width=40)

    for i, record in enumerate(reversed(history[-30:])):
        status = "[green]✓[/green]" if record["status"] == "成功" else "[red]✗[/red]"
        table.add_row(
            str(len(history) - i),
            record.get("time", ""),
            (record.get("title", "") or "")[:28],
            status,
            (record.get("url", "") or "")[:38],
        )

    print(table)

    action = questionary.select(
        "",
        choices=[
            questionary.Choice(title="返回主菜单", value="back"),
            questionary.Choice(title="清空历史记录", value="clear"),
        ],
        qmark="",
    ).ask()

    if action == "clear":
        confirm = questionary.confirm("确定清空所有下载历史？", default=False).ask()
        if confirm:
            clear_history()
            print("[green]  历史记录已清空[/green]")
            questionary.press_any_key_to_continue("按 Enter 返回...").ask()


def _handle_settings(cfg: dict):
    """Settings menu."""
    ffmpeg_status = "[green]✓ 已检测[/green]" if cfg.get("ffmpeg_path") else "[red]✗ 未安装[/red]"

    while True:
        console.clear()
        print(Panel.fit("[bold]  设置  [/bold]", width=60))

        current = [
            f"  输出目录:      {cfg['output_dir']}",
            f"  代理:          {cfg['proxy'] or '无'}",
            f"  Cookie 文件:   {cfg['cookie_file'] or '无'}",
            f"  默认格式:      {cfg['default_format']}",
            f"  合并格式:      {cfg['merge_format']}",
            f"  音频格式:      {cfg['audio_format']}",
            f"  字幕语言:      {cfg['subtitle_lang']}",
            f"  下载字幕:      {'是' if cfg['download_subs'] else '否'}",
            f"  嵌入缩略图:    {'是' if cfg['embed_thumbnail'] else '否'}",
            f"  并发数:        {cfg['concurrent']}",
            f"  最大重试:      {cfg['max_retries']}",
            f"  断点续传:      {'是' if cfg['continue_dl'] else '否'}",
            f"  ffmpeg:        {ffmpeg_status}",
        ]
        print("\n".join(current))
        print()

        choice = questionary.select(
            "选择要修改的配置:",
            choices=[
                questionary.Choice(title="输出目录", value="output_dir"),
                questionary.Choice(title="代理地址", value="proxy"),
                questionary.Choice(title="Cookie 文件路径", value="cookie"),
                questionary.Choice(title="默认下载格式", value="format"),
                questionary.Choice(title="合并输出格式", value="merge_format"),
                questionary.Choice(title="音频提取格式", value="audio_format"),
                questionary.Choice(title="字幕语言", value="subtitle"),
                questionary.Choice(title="嵌入缩略图", value="thumbnail"),
                questionary.Choice(title="并发下载数", value="concurrent"),
                questionary.Choice(title="最大重试次数", value="retries"),
                questionary.Choice(title="断点续传", value="continue"),
                questionary.Separator(),
                questionary.Choice(title="返回主菜单 (保存)", value="back"),
            ],
            qmark="",
        ).ask()

        if choice == "back":
            save_config(cfg)
            break
        elif choice == "output_dir":
            val = questionary.text("输出目录:", default=cfg["output_dir"]).ask()
            if val:
                cfg["output_dir"] = val
        elif choice == "proxy":
            val = questionary.text("代理地址 (如 socks5://127.0.0.1:1080，留空清除):", default=cfg.get("proxy", "")).ask()
            cfg["proxy"] = val.strip() if val else ""
        elif choice == "cookie":
            val = questionary.text("Cookie 文件路径 (Netscape 格式，留空清除):", default=cfg.get("cookie_file", "")).ask()
            cfg["cookie_file"] = val.strip() if val else ""
        elif choice == "format":
            val = questionary.text(
                "默认格式:",
                default=cfg.get("default_format", "bestvideo+bestaudio/best"),
                instruction="(bestvideo+bestaudio/best, best, worst, 或指定格式ID)",
            ).ask()
            if val:
                cfg["default_format"] = val
        elif choice == "merge_format":
            val = questionary.select(
                "合并输出格式:",
                choices=["mp4", "mkv", "webm"],
                default=cfg.get("merge_format", "mp4"),
            ).ask()
            if val:
                cfg["merge_format"] = val
        elif choice == "audio_format":
            val = questionary.select(
                "音频格式:",
                choices=["mp3", "m4a", "opus", "wav", "flac"],
                default=cfg.get("audio_format", "mp3"),
            ).ask()
            if val:
                cfg["audio_format"] = val
        elif choice == "subtitle":
            val = questionary.text("字幕语言 (逗号分隔，如 zh,en):", default=cfg.get("subtitle_lang", "zh,en")).ask()
            if val is not None:
                cfg["subtitle_lang"] = val
        elif choice == "thumbnail":
            val = questionary.confirm("下载后嵌入缩略图？", default=cfg.get("embed_thumbnail", True)).ask()
            cfg["embed_thumbnail"] = val
        elif choice == "concurrent":
            val = questionary.text("并发下载数:", default=str(cfg.get("concurrent", 3))).ask()
            if val and val.isdigit() and 1 <= int(val) <= 10:
                cfg["concurrent"] = int(val)
        elif choice == "retries":
            val = questionary.text("最大重试次数:", default=str(cfg.get("max_retries", 3))).ask()
            if val and val.isdigit():
                cfg["max_retries"] = int(val)
        elif choice == "continue":
            val = questionary.confirm("启用断点续传？", default=cfg.get("continue_dl", True)).ask()
            cfg["continue_dl"] = val
