"""CLI interactive menu."""

import os
from pathlib import Path
from urllib.parse import urlparse

import questionary
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import QUALITY_PRESETS, load_config, save_config
from grabber.ytdlp_wrapper import (
    download_playlist,
    download_url,
    get_metadata,
    get_playlist_entries,
    list_formats,
)
from grabber.page_parser import discover_from_url
from grabber.browser_scraper import scrape_sync
from grabber.feed_parser import parse_feed_url
from grabber.site_crawler import crawl_site, parse_sitemap
from utils.formatter import (
    extract_height,
    filter_formats_by_max_height,
    format_size,
    quality_to_max_height,
    quality_to_ytdlp_format,
    sort_formats_by_quality,
)
from utils.history import add_record, clear_history, load_history
from cli.progress import ProgressHook, create_progress

console = Console()

# ordered quality keys shown in the picker (best → lowest)
QUALITY_KEYS = list(QUALITY_PRESETS.keys())


# ── helpers ────────────────────────────────────────────────────────────────

def _pick_quality(cfg: dict) -> str:
    """Ask user to select a quality preset. Returns the quality key."""
    default_q = cfg.get("preferred_quality", "best")
    if default_q not in QUALITY_PRESETS:
        default_q = "best"

    labels = [
        questionary.Choice(
            title=QUALITY_PRESETS[k]["label"],
            value=k,
        )
        for k in QUALITY_KEYS
    ]

    return questionary.select(
        "选择画质:",
        choices=labels,
        default=default_q,
        qmark="",
    ).ask() or "best"


def _pick_output_dir(cfg: dict) -> str | None:
    """Ask for output directory; returns None if user cancels."""
    d = questionary.text("输出目录:", default=cfg["output_dir"]).ask()
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
    return d


# ═══════════════════════════════════════════════════════════════════════════
#  Main menu
# ═══════════════════════════════════════════════════════════════════════════

def main_menu():
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
                questionary.Choice(title="🔍  高级抓取", value="advanced"),
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
        elif choice == "advanced":
            _handle_advanced(cfg)
        elif choice == "history":
            _show_history()
        elif choice == "settings":
            _handle_settings(cfg)


# ═══════════════════════════════════════════════════════════════════════════
#  Core download flow
# ═══════════════════════════════════════════════════════════════════════════

def _handle_download(cfg: dict, audio_only: bool = False):
    """Core download: yt-dlp first, then page-parser fallback."""
    console.clear()
    url = questionary.text(
        "输入视频页面 URL:" if not audio_only else "输入音频/视频页面 URL (仅提取音频):",
        validate=lambda t: True if t.strip() else "URL 不能为空",
    ).ask()
    if not url:
        return

    mode_label = "音频" if audio_only else "视频"
    print(f"\n[cyan]  正在分析 ({mode_label}模式)...[/cyan]")

    # ── Phase 1: yt-dlp ──
    print("[dim]  尝试 yt-dlp 解析...[/dim]")
    meta = get_metadata(url, proxy=cfg.get("proxy", ""))
    formats = list_formats(url, proxy=cfg.get("proxy", ""))
    ytdlp_worked = bool(formats)

    # ── Phase 2: page-parser (supplementary / fallback) ──
    if not ytdlp_worked:
        print("[dim]  yt-dlp 无结果，启动智能页面扫描...[/dim]")
    discovery = discover_from_url(url, timeout=15, follow_iframes=True)
    discovered = discovery.get("videos", [])

    # merge metadata when yt-dlp returned nothing
    if not meta or not meta.get("title"):
        if discovery.get("page_title"):
            meta = meta or {}
            meta["title"] = discovery["page_title"]
            meta["description"] = ""
            meta["duration"] = "未知"
            meta["uploader"] = ""
            meta["thumbnail"] = discovery.get("thumbnail") or ""

    if meta and meta.get("title"):
        _show_meta_info(meta)

    # ── Phase 3: route ──
    if ytdlp_worked:
        _download_via_ytdlp(url, formats, meta, cfg, audio_only,
                            discovered_count=len(discovered))
        return

    if not discovered:
        print("[red]  未能检测到任何视频资源[/red]")
        print("[dim]  提示:[/dim]")
        print("[dim]    1. 确认 URL 是否正确[/dim]")
        print("[dim]    2. 浏览器 F12 → Network → 搜索 .mp4 / .m3u8[/dim]")
        print("[dim]    3. 复制直接视频链接后重试[/dim]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    _show_discovered_table(discovery, discovered)
    _download_discovered(discovered, meta, cfg, audio_only)


# ═══════════════════════════════════════════════════════════════════════════
#  yt-dlp path — quality selection → format pick → download
# ═══════════════════════════════════════════════════════════════════════════

def _download_via_ytdlp(url, formats, meta, cfg, audio_only, discovered_count=0):
    """yt-dlp download with quality-picker and optional format refinement."""

    # optional: show page-parser discoveries
    if discovered_count:
        print(f"[dim]  页面解析器另外发现 {discovered_count} 个潜在视频源[/dim]")
        show_extra = questionary.confirm(
            "是否查看页面解析器发现的额外视频源?", default=False
        ).ask()
        if show_extra:
            discovery = discover_from_url(url, timeout=15, follow_iframes=True)
            disc = discovery.get("videos", [])
            if disc:
                _show_discovered_table(discovery, disc)
                if questionary.confirm("使用页面发现的视频源?", default=False).ask():
                    _download_discovered(disc, meta, cfg, audio_only)
                    return

    # ── quality picker ──
    quality_key = _pick_quality(cfg)
    max_height = quality_to_max_height(quality_key)

    # filter & sort
    filtered = filter_formats_by_max_height(formats, max_height)
    filtered = sort_formats_by_quality(filtered)

    if not filtered:
        print(f"[yellow]  当前画质 {quality_key} 下无可选格式，显示全部[/yellow]")
        filtered = sort_formats_by_quality(formats)

    _show_format_table(filtered)

    if audio_only:
        output_dir = _pick_output_dir(cfg)
        if not output_dir:
            return
        _do_audio_download(url, output_dir, meta, cfg)
        return

    # format selection
    choices = []
    for i, f in enumerate(filtered):
        height = extract_height(f.get("resolution")) or 0
        note = f.get("note", "")
        tag = ""
        if height >= 2160:
            tag = " [4K]"
        elif height >= 1080:
            tag = " [FHD]"
        elif height >= 720:
            tag = " [HD]"
        choices.append(
            questionary.Choice(
                title=f"{f['resolution']:>12}  {f['ext']:>5}  {format_size(f['filesize']):>10}  {note:12}{tag}  [{f['format_id']}]",
                value=i,
            )
        )

    # prepend auto-choice (best at selected quality)
    auto_label = f"自动 — {QUALITY_PRESETS.get(quality_key, {}).get('label', quality_key)}"
    choices.insert(0, questionary.Choice(title=auto_label, value=-1))

    selected = questionary.select(
        "选择要下载的视频格式:",
        choices=choices,
        qmark="",
    ).ask()
    if selected is None:
        return

    output_dir = _pick_output_dir(cfg)
    if not output_dir:
        return

    if selected == -1:
        format_spec = quality_to_ytdlp_format(quality_key)
    else:
        format_spec = filtered[selected]["format_id"]

    _do_video_download(url, output_dir, format_spec, meta, cfg)


# ═══════════════════════════════════════════════════════════════════════════
#  Discovered-URL download path
# ═══════════════════════════════════════════════════════════════════════════

def _download_discovered(discovered: list[dict], meta: dict, cfg: dict,
                         audio_only: bool = False):
    """Let user pick which discovered video URLs to download."""
    if len(discovered) == 1:
        chosen = [0]
    else:
        choices = [
            questionary.Choice(
                title=f"[{v['type']:16}] {v['label']:20}  {v['url'][:60]}",
                value=i,
            )
            for i, v in enumerate(discovered)
        ]
        chosen = questionary.checkbox(
            "选择要下载的视频源 (空格勾选，Enter 确认):",
            choices=choices,
            qmark="",
        ).ask()
        if not chosen:
            return

    output_dir = _pick_output_dir(cfg)
    if not output_dir:
        return

    title = meta.get("title", "")
    success_count = 0
    for i in chosen:
        v = discovered[i]
        vid_url = v["url"]
        vid_type = v["type"]
        print(f"\n[cyan]  下载 [{i+1}/{len(chosen)}]: {vid_url[:60]}[/cyan]")
        print(f"  来源: {v['label']}")

        if vid_type == "iframe_embed" or v.get("ext") == "m3u8" or "m3u8" in vid_url.lower():
            ok = _download_with_ytdlp(vid_url, output_dir, cfg, meta)
        elif v.get("ext") in ("mp4", "mkv", "webm", "flv", "mov", "avi", "wmv"):
            ok = _download_direct(vid_url, output_dir, title, cfg)
        else:
            ok = _download_with_ytdlp(vid_url, output_dir, cfg, meta)
            if not ok:
                ok = _download_direct(vid_url, output_dir, title, cfg)

        if ok:
            success_count += 1
            add_record(vid_url, title or vid_url, output_dir, "成功")
        else:
            add_record(vid_url, title or vid_url, output_dir, "失败")

    print(f"\n[green]  完成: {success_count}/{len(chosen)} 成功[/green]")
    questionary.press_any_key_to_continue("按 Enter 返回主菜单...").ask()


# ═══════════════════════════════════════════════════════════════════════════
#  Download executors
# ═══════════════════════════════════════════════════════════════════════════

def _download_with_ytdlp(url: str, output_dir: str, cfg: dict, meta: dict) -> bool:
    """Download using yt-dlp with quality preset + progress bar."""
    quality_key = cfg.get("preferred_quality", "best")
    format_spec = quality_to_ytdlp_format(quality_key)

    progress = create_progress()
    with progress:
        task_id = progress.add_task(
            f"下载: {meta.get('title', url)[:40]}", total=None
        )
        hook = ProgressHook(progress, task_id)
        return download_url(
            url, output_dir,
            format_spec=format_spec,
            proxy=cfg.get("proxy", ""),
            cookie_file=cfg.get("cookie_file", ""),
            merge_format=cfg.get("merge_format", "mp4"),
            max_retries=cfg.get("max_retries", 3),
            continue_dl=cfg.get("continue_dl", True),
            ffmpeg_path=cfg.get("ffmpeg_path", ""),
            progress_hooks=[hook],
        )


def _download_direct(url: str, output_dir: str, title: str, cfg: dict) -> bool:
    """Direct HTTP download with progress bar."""
    import requests as req
    from utils.formatter import safe_filename

    filename = safe_filename(title or urlparse(url).path.rsplit("/", 1)[-1] or "video")
    ext = urlparse(url).path.rsplit(".", 1)[-1].split("?")[0] or "mp4"
    dest = str(Path(output_dir) / f"{filename}.{ext}")

    print(f"[dim]  直接下载模式 → {dest}[/dim]")
    progress = create_progress()
    try:
        with progress:
            resp = req.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                    "Referer": url,
                },
                stream=True,
                timeout=(15, 600),
            )
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            task_id = progress.add_task(
                f"下载: {filename[:40]}", total=total or None
            )
            downloaded = 0
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        progress.update(task_id, completed=downloaded, total=total)
            if total:
                progress.update(task_id, completed=total, total=total)
        return True
    except Exception as e:
        print(f"[red]  直接下载失败: {e}[/red]")
        return False


def _do_video_download(url, output_dir, format_spec, meta, cfg):
    """Execute yt-dlp video download with progress."""
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
        print(f"\n[green]  ✅ 下载完成! → {output_dir}[/green]")
        add_record(url, meta.get("title", url), output_dir, "成功")
    else:
        print(f"\n[red]  ❌ 下载失败[/red]")
        add_record(url, meta.get("title", url), output_dir, "失败")
    questionary.press_any_key_to_continue("按 Enter 返回主菜单...").ask()


def _do_audio_download(url, output_dir, meta, cfg):
    """Execute audio-only extraction."""
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
    if ok:
        print(f"\n[green]  ✅ 音频提取完成! → {output_dir}[/green]")
        add_record(url, meta.get("title", url), output_dir, "成功")
    else:
        print(f"\n[red]  ❌ 提取失败[/red]")
        add_record(url, meta.get("title", url), output_dir, "失败")
    questionary.press_any_key_to_continue("按 Enter 返回主菜单...").ask()


# ═══════════════════════════════════════════════════════════════════════════
#  Display helpers
# ═══════════════════════════════════════════════════════════════════════════

def _show_meta_info(meta: dict):
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan", width=10)
    table.add_column(style="white")
    if meta.get("title"):
        table.add_row("标题:", meta["title"])
    if meta.get("duration"):
        table.add_row("时长:", meta["duration"])
    if meta.get("uploader"):
        table.add_row("上传者:", meta["uploader"])
    if meta.get("description"):
        table.add_row("简介:", meta["description"][:100])
    print(table)


def _show_format_table(formats: list[dict]):
    table = Table(title=f"可用格式 ({len(formats)} 个)")
    table.add_column("#", style="dim", width=4)
    table.add_column("分辨率", style="cyan")
    table.add_column("扩展名", style="green")
    table.add_column("大小", justify="right")
    table.add_column("编码", style="dim")
    table.add_column("备注")
    for i, f in enumerate(formats):
        vcodec = f.get("vcodec", "?")
        acodec = f.get("acodec", "?")
        codec = ""
        if vcodec and vcodec != "none":
            codec += vcodec[:6]
        if acodec and acodec != "none":
            if codec:
                codec += "+"
            codec += acodec[:6]
        table.add_row(
            str(i + 1),
            f.get("resolution", "?"),
            f.get("ext", "?"),
            format_size(f.get("filesize")),
            codec or "?",
            f.get("note", ""),
        )
    print(table)


def _show_discovered_table(discovery: dict, videos: list[dict]):
    print(f"\n[yellow]  页面标题: {discovery.get('page_title', '未知')}[/yellow]")
    table = Table(title=f"从页面中发现 {len(videos)} 个视频源")
    table.add_column("#", style="dim", width=4)
    table.add_column("来源类型", style="cyan", width=16)
    table.add_column("标签", style="green", width=20)
    table.add_column("URL", style="dim", width=60)
    for i, v in enumerate(videos):
        table.add_row(str(i + 1), v.get("type", "?"), v.get("label", ""), v["url"][:58])
    print(table)


# ═══════════════════════════════════════════════════════════════════════════
#  Playlist / Batch / History / Settings
# ═══════════════════════════════════════════════════════════════════════════

def _handle_playlist(cfg: dict):
    console.clear()
    url = questionary.text("输入播放列表 URL:",
                           validate=lambda t: True if t.strip() else "不能为空").ask()
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

    if not questionary.confirm(
        f"\n确定下载全部 {len(entries)} 个视频？", default=False
    ).ask():
        return

    # quality picker
    quality_key = _pick_quality(cfg)
    format_spec = quality_to_ytdlp_format(quality_key)
    print(f"[dim]  画质: {QUALITY_PRESETS[quality_key]['label']}[/dim]")

    output_dir = _pick_output_dir(cfg)
    if not output_dir:
        return

    print(f"\n[cyan]  开始下载播放列表...[/cyan]")
    progress = create_progress()
    with progress:
        task_id = progress.add_task("播放列表下载", total=len(entries))
        hook = ProgressHook(progress, task_id)
        success, total = download_playlist(
            url, output_dir, format_spec=format_spec,
            proxy=cfg.get("proxy", ""),
            cookie_file=cfg.get("cookie_file", ""),
            merge_format=cfg.get("merge_format", "mp4"),
            max_retries=cfg.get("max_retries", 3),
            ffmpeg_path=cfg.get("ffmpeg_path", ""),
            progress_hooks=[hook],
        )
        progress.update(task_id, completed=total)

    print(f"\n[green]  播放列表下载完成: {success}/{total} 成功[/green]")
    add_record(url, f"播放列表 ({success}/{total})", output_dir,
               "成功" if success == total else "部分成功")
    questionary.press_any_key_to_continue("按 Enter 返回...").ask()


def _handle_batch(cfg: dict):
    console.clear()
    file_path = questionary.path("输入 URL 列表文件路径:").ask()
    if not file_path or not os.path.exists(file_path):
        print("[red]  文件不存在[/red]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    with open(file_path, encoding="utf-8") as f:
        urls = [line.strip() for line in f
                if line.strip() and not line.startswith("#")]

    print(f"[cyan]  共发现 {len(urls)} 个链接[/cyan]")

    # quality picker
    quality_key = _pick_quality(cfg)
    format_spec = quality_to_ytdlp_format(quality_key)
    print(f"[dim]  画质: {QUALITY_PRESETS[quality_key]['label']}[/dim]")

    output_dir = _pick_output_dir(cfg)
    if not output_dir:
        return

    success = 0
    progress = create_progress()
    with progress:
        task_id = progress.add_task("批量下载", total=len(urls))
        for i, url in enumerate(urls):
            progress.update(task_id, description=f"批量下载 [{i+1}/{len(urls)}]")
            ok = download_url(
                url, output_dir, format_spec=format_spec,
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


# ═══════════════════════════════════════════════════════════════════════════
#  Advanced scraping — browser / feed / crawler / sitemap
# ═══════════════════════════════════════════════════════════════════════════

def _handle_advanced(cfg: dict):
    """Advanced scraping submenu."""
    while True:
        console.clear()
        print(Panel.fit("[bold magenta]  高级抓取  [/bold magenta]", width=60))

        choice = questionary.select(
            "选择抓取方式:",
            choices=[
                questionary.Choice(title="🌐  浏览器抓取 (Playwright)", value="browser"),
                questionary.Choice(title="📡  RSS/Atom 订阅源解析", value="feed"),
                questionary.Choice(title="🕷️  网站爬虫 (递归发现)", value="crawl"),
                questionary.Choice(title="🗺️  Sitemap 解析", value="sitemap"),
                questionary.Separator(),
                questionary.Choice(title="返回主菜单", value="back"),
            ],
            qmark="",
        ).ask()

        if choice == "back":
            break
        elif choice == "browser":
            _handle_browser_scrape(cfg)
        elif choice == "feed":
            _handle_feed_parse(cfg)
        elif choice == "crawl":
            _handle_crawl_site(cfg)
        elif choice == "sitemap":
            _handle_sitemap(cfg)


def _handle_browser_scrape(cfg: dict):
    """Playwright-based browser scraping for JS-rendered pages."""
    console.clear()
    url = questionary.text(
        "输入页面 URL (将使用无头浏览器渲染):",
        validate=lambda t: True if t.strip() else "URL 不能为空",
    ).ask()
    if not url:
        return

    headless = questionary.confirm(
        "使用无头模式?", default=cfg.get("browser_headless", True)
    ).ask()

    print(f"\n[cyan]  启动浏览器，正在渲染页面...[/cyan]")
    print("[dim]  (首次运行需安装 Chromium: playwright install chromium)[/dim]")

    result = scrape_sync(
        url,
        headless=headless,
        timeout_ms=cfg.get("browser_timeout", 30) * 1000,
        scroll_to_bottom=True,
        proxy=cfg.get("proxy", ""),
    )

    if result.get("error"):
        print(f"[red]  {result['error']}[/red]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    title = result.get("page_title", "")
    print(f"\n[yellow]  页面标题: {title}[/yellow]")

    # merge all discovered URLs
    all_videos = result.get("dom_videos", []) + result.get("dom_audio", [])
    net_urls = result.get("network_videos", [])
    for u in net_urls:
        all_videos.append({"url": u, "type": "network", "ext": "", "label": "网络拦截"})

    if not all_videos:
        print("[red]  未发现视频资源[/red]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    # show discovered
    table = Table(title=f"浏览器发现 {len(all_videos)} 个资源")
    table.add_column("#", style="dim", width=4)
    table.add_column("来源", style="cyan", width=16)
    table.add_column("类型", style="green", width=10)
    table.add_column("URL", style="dim", width=60)
    for i, v in enumerate(all_videos):
        table.add_row(str(i+1), v.get("label", ""), v.get("ext", ""), v["url"][:58])
    print(table)

    choices = [
        questionary.Choice(
            title=f"[{v.get('label', ''):12}] {v['url'][:50]}",
            value=i,
        )
        for i, v in enumerate(all_videos)
    ]
    chosen = questionary.checkbox(
        "选择要下载的资源 (空格勾选):",
        choices=choices,
        qmark="",
    ).ask()
    if not chosen:
        return

    output_dir = _pick_output_dir(cfg)
    if not output_dir:
        return

    success = 0
    for i in chosen:
        v = all_videos[i]
        vu = v["url"]
        print(f"\n[cyan]  下载: {vu[:60]}[/cyan]")
        if v.get("ext") == "m3u8" or "m3u8" in vu:
            ok = _download_with_ytdlp(vu, output_dir, cfg, {"title": title})
        else:
            ok = _download_direct(vu, output_dir, title or vu, cfg)
        if ok:
            success += 1
            add_record(vu, title, output_dir, "成功")
        else:
            add_record(vu, title, output_dir, "失败")

    print(f"\n[green]  完成: {success}/{len(chosen)} 成功[/green]")
    questionary.press_any_key_to_continue("按 Enter 返回...").ask()


def _handle_feed_parse(cfg: dict):
    """Parse RSS/Atom feed for video enclosures."""
    console.clear()
    feed_url = questionary.text(
        "输入 RSS/Atom 订阅源 URL:",
        validate=lambda t: True if t.strip() else "URL 不能为空",
    ).ask()
    if not feed_url:
        return

    print(f"\n[cyan]  正在解析订阅源...[/cyan]")
    feed = parse_feed_url(feed_url)
    feed_title = feed.get("feed_title", "未知")
    entries = feed.get("entries", [])

    if not entries:
        print("[red]  未解析到条目[/red]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    print(f"\n[green]  {feed_title}[/green]")
    print(f"[green]  共 {len(entries)} 个条目[/green]")

    # show entries with video counts
    table = Table(title="订阅源条目")
    table.add_column("#", style="dim", width=4)
    table.add_column("标题", style="cyan", width=35)
    table.add_column("视频数", style="green")
    table.add_column("发布时间", style="dim", width=20)
    for i, entry in enumerate(entries[:20]):
        table.add_row(
            str(i+1),
            entry.get("title", "")[:33],
            str(len(entry.get("videos", []))),
            entry.get("published", "")[:18],
        )
    if len(entries) > 20:
        print(f"[dim]  ... 还有 {len(entries) - 20} 条[/dim]")
    print(table)

    # collect all video URLs
    all_videos = []
    for entry in entries:
        for v in entry.get("videos", []):
            all_videos.append({**v, "entry_title": entry.get("title", "")})

    if not all_videos:
        print("[yellow]  条目中没有视频附件，但可以尝试访问原页面抓取[/yellow]")
        pick = questionary.select(
            "选择操作:",
            choices=[
                questionary.Choice(title="选择条目，尝试从原页面抓取", value="page"),
                questionary.Choice(title="返回", value="back"),
            ],
        ).ask()
        if pick == "page":
            entry_choices = [
                questionary.Choice(
                    title=f"{e.get('title', '无标题')[:50]}  ({e.get('published', '')})",
                    value=i,
                )
                for i, e in enumerate(entries[:20])
            ]
            selected = questionary.checkbox(
                "选择条目 (将逐个抓取原页面):",
                choices=entry_choices,
                qmark="",
            ).ask()
            if selected:
                output_dir = _pick_output_dir(cfg)
                if output_dir:
                    for i in selected:
                        page_url = entries[i].get("url", "")
                        if page_url:
                            print(f"[cyan]  抓取: {page_url}[/cyan]")
                            _handle_download_from_url(page_url, output_dir, cfg)
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    # show discovered videos
    table2 = Table(title=f"发现 {len(all_videos)} 个视频")
    table2.add_column("#", style="dim", width=4)
    table2.add_column("来源条目", style="cyan", width=25)
    table2.add_column("格式", style="green")
    table2.add_column("URL", style="dim", width=50)
    for i, v in enumerate(all_videos):
        table2.add_row(str(i+1), v.get("entry_title", "")[:23], v.get("ext", ""), v["url"][:48])
    print(table2)

    v_choices = [
        questionary.Choice(title=f"[{v.get('ext', '?'):5}] {v['url'][:55]}", value=i)
        for i, v in enumerate(all_videos)
    ]
    chosen = questionary.checkbox("选择要下载的视频:", choices=v_choices, qmark="").ask()
    if not chosen:
        return

    output_dir = _pick_output_dir(cfg)
    if not output_dir:
        return

    success = 0
    for i in chosen:
        v = all_videos[i]
        vu = v["url"]
        print(f"[cyan]  下载: {vu[:60]}[/cyan]")
        ok = _download_direct(vu, output_dir, v.get("entry_title", vu), cfg)
        if ok:
            success += 1
            add_record(vu, feed_title, output_dir, "成功")
        else:
            add_record(vu, feed_title, output_dir, "失败")

    print(f"\n[green]  完成: {success}/{len(chosen)} 成功[/green]")
    questionary.press_any_key_to_continue("按 Enter 返回...").ask()


def _handle_crawl_site(cfg: dict):
    """Recursive site crawler to find video pages."""
    console.clear()
    start_url = questionary.text(
        "输入起始 URL (将递归爬取同域名下的页面):",
        validate=lambda t: True if t.strip() else "URL 不能为空",
    ).ask()
    if not start_url:
        return

    max_pages = questionary.text(
        "最大爬取页数:", default=str(cfg.get("crawler_max_pages", 50))
    ).ask()
    max_pages = int(max_pages) if max_pages and max_pages.isdigit() else 50

    max_depth = questionary.text(
        "最大深度:", default=str(cfg.get("crawler_max_depth", 3))
    ).ask()
    max_depth = int(max_depth) if max_depth and max_depth.isdigit() else 3

    print(f"\n[cyan]  开始爬取 (最多 {max_pages} 页, 深度 {max_depth})...[/cyan]")
    print("[dim]  这可能需要几分钟...[/dim]")

    results = crawl_site(
        start_url,
        max_pages=max_pages,
        max_depth=max_depth,
        concurrency=cfg.get("crawler_concurrency", 5),
        proxy=cfg.get("proxy", ""),
    )

    if not results:
        print("[red]  未发现包含视频的页面[/red]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    print(f"\n[green]  发现 {len(results)} 个包含视频的页面:[/green]")

    table = Table(title="爬取结果")
    table.add_column("#", style="dim", width=4)
    table.add_column("标题", style="cyan", width=35)
    table.add_column("视频数", style="green")
    table.add_column("深度", style="dim")
    table.add_column("URL", style="dim", width=45)
    for i, r in enumerate(results[:30]):
        table.add_row(str(i+1), r.title[:33], str(r.video_count), str(r.depth), r.url[:43])
    if len(results) > 30:
        print(f"[dim]  ... 还有 {len(results) - 30} 个页面[/dim]")
    print(table)

    pick = questionary.select(
        "",
        choices=[
            questionary.Choice(title="选择页面，逐个下载其中的视频", value="select"),
            questionary.Choice(title="下载所有发现的视频 (可能非常多)", value="all"),
            questionary.Choice(title="返回", value="back"),
        ],
        qmark="",
    ).ask()

    if pick == "back":
        return

    output_dir = _pick_output_dir(cfg)
    if not output_dir:
        return

    if pick == "all":
        pages_to_dl = results
    else:
        p_choices = [
            questionary.Choice(
                title=f"{r.title[:40]} ({r.video_count}个视频)",
                value=i,
            )
            for i, r in enumerate(results[:30])
        ]
        selected = questionary.checkbox(
            "选择页面:", choices=p_choices, qmark=""
        ).ask()
        if not selected:
            return
        pages_to_dl = [results[i] for i in selected]

    total_ok = 0
    total_all = 0
    for page in pages_to_dl:
        for v in page.videos:
            total_all += 1
            vu = v["url"]
            print(f"[cyan]  下载: {vu[:60]}[/cyan]")
            ok = _download_direct(vu, output_dir, page.title or vu, cfg)
            if ok:
                total_ok += 1
                add_record(vu, page.title, output_dir, "成功")
            else:
                add_record(vu, page.title, output_dir, "失败")

    print(f"\n[green]  完成: {total_ok}/{total_all} 成功[/green]")
    questionary.press_any_key_to_continue("按 Enter 返回...").ask()


def _handle_sitemap(cfg: dict):
    """Parse sitemap.xml to discover video URLs."""
    console.clear()
    domain = questionary.text(
        "输入网站域名或 URL:",
        validate=lambda t: True if t.strip() else "不能为空",
    ).ask()
    if not domain:
        return

    print(f"\n[cyan]  正在解析 sitemap...[/cyan]")
    urls = parse_sitemap(domain)

    if not urls:
        print("[red]  未从 sitemap 中解析到 URL[/red]")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    print(f"[green]  从 sitemap 中发现 {len(urls)} 个 URL[/green]")

    pick = questionary.select(
        f"共 {len(urls)} 个 URL，选择操作:",
        choices=[
            questionary.Choice(title=f"全部逐个分析，下载发现的视频", value="all"),
            questionary.Choice(title=f"只显示 URL 列表", value="list"),
            questionary.Choice(title="返回", value="back"),
        ],
        qmark="",
    ).ask()

    if pick == "back":
        return
    elif pick == "list":
        for i, u in enumerate(urls[:50]):
            print(f"  {i+1:>4}. {u}")
        if len(urls) > 50:
            print(f"  ... 还有 {len(urls) - 50} 个")
        questionary.press_any_key_to_continue("按 Enter 返回...").ask()
        return

    output_dir = _pick_output_dir(cfg)
    if not output_dir:
        return

    # analyze each URL for video content
    total_found = 0
    total_downloaded = 0
    progress = create_progress()
    with progress:
        task_id = progress.add_task("分析 sitemap URLs", total=min(len(urls), 100))
        for i, u in enumerate(urls[:100]):
            progress.update(task_id, description=f"分析 [{i+1}/{min(len(urls), 100)}]")
            discovery = discover_from_url(u, timeout=10, follow_iframes=False)
            videos = discovery.get("videos", [])
            total_found += len(videos)
            for v in videos:
                ok = _download_direct(v["url"], output_dir,
                                      discovery.get("page_title", v["url"]), cfg)
                if ok:
                    total_downloaded += 1
            progress.advance(task_id)

    print(f"\n[green]  完成: 分析了 {min(len(urls), 100)} 个 URL, "
          f"发现 {total_found} 个视频, 下载 {total_downloaded} 个[/green]")
    questionary.press_any_key_to_continue("按 Enter 返回...").ask()


def _handle_download_from_url(url: str, output_dir: str, cfg: dict):
    """Quick download: analyze a page and download its first video."""
    discovery = discover_from_url(url, timeout=15, follow_iframes=True)
    videos = discovery.get("videos", [])
    if not videos:
        print(f"  [dim]  未发现视频[/dim]")
        return
    for v in videos[:3]:
        vu = v["url"]
        ok = _download_direct(vu, output_dir,
                              discovery.get("page_title", url), cfg)
        if ok:
            add_record(vu, discovery.get("page_title", ""), output_dir, "成功")


def _show_history():
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
    ffmpeg_status = "[green]✓ 已检测[/green]" if cfg.get("ffmpeg_path") else "[red]✗ 未安装[/red]"
    cur_q = cfg.get("preferred_quality", "best")
    q_label = QUALITY_PRESETS.get(cur_q, {}).get("label", cur_q)

    while True:
        console.clear()
        print(Panel.fit("[bold]  设置  [/bold]", width=60))

        lines = [
            f"  输出目录:      {cfg['output_dir']}",
            f"  默认画质:      {q_label}",
            f"  最大文件大小:  {cfg.get('max_filesize_mb', 0) or '不限制'} MB",
            f"  代理:          {cfg['proxy'] or '无'}",
            f"  Cookie 文件:   {cfg['cookie_file'] or '无'}",
            f"  合并格式:      {cfg['merge_format']}",
            f"  音频格式:      {cfg['audio_format']}",
            f"  字幕语言:      {cfg['subtitle_lang']}",
            f"  下载字幕:      {'是' if cfg['download_subs'] else '否'}",
            f"  嵌入缩略图:    {'是' if cfg['embed_thumbnail'] else '否'}",
            f"  并发数:        {cfg['concurrent']}",
            f"  最大重试:      {cfg['max_retries']}",
            f"  断点续传:      {'是' if cfg['continue_dl'] else '否'}",
            f"  ffmpeg:        {ffmpeg_status}",
            f"  ── 爬虫设置 ──",
            f"  浏览器无头:    {'是' if cfg.get('browser_headless', True) else '否'}",
            f"  爬虫最大页数:  {cfg.get('crawler_max_pages', 50)}",
            f"  爬虫最大深度:  {cfg.get('crawler_max_depth', 3)}",
        ]
        print("\n".join(lines))
        print()

        choice = questionary.select(
            "选择要修改的配置:",
            choices=[
                questionary.Choice(title="输出目录", value="output_dir"),
                questionary.Choice(title="默认画质", value="quality"),
                questionary.Choice(title="最大文件大小", value="max_size"),
                questionary.Choice(title="代理地址", value="proxy"),
                questionary.Choice(title="Cookie 文件路径", value="cookie"),
                questionary.Choice(title="合并输出格式", value="merge_format"),
                questionary.Choice(title="音频提取格式", value="audio_format"),
                questionary.Choice(title="字幕语言", value="subtitle"),
                questionary.Choice(title="嵌入缩略图", value="thumbnail"),
                questionary.Choice(title="并发下载数", value="concurrent"),
                questionary.Choice(title="最大重试次数", value="retries"),
                questionary.Choice(title="断点续传", value="continue"),
                questionary.Separator(),
                questionary.Choice(title="浏览器无头模式", value="browser_headless"),
                questionary.Choice(title="爬虫最大页数", value="crawler_max_pages"),
                questionary.Choice(title="爬虫最大深度", value="crawler_max_depth"),
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
        elif choice == "quality":
            val = questionary.select(
                "默认画质:",
                choices=[questionary.Choice(title=v["label"], value=k)
                         for k, v in QUALITY_PRESETS.items()],
                default=cur_q,
            ).ask()
            if val:
                cfg["preferred_quality"] = val
                cur_q = val
                q_label = QUALITY_PRESETS[val]["label"]
        elif choice == "max_size":
            val = questionary.text(
                "最大文件大小 (MB, 0=不限制):",
                default=str(cfg.get("max_filesize_mb", 0)),
            ).ask()
            if val and val.isdigit():
                cfg["max_filesize_mb"] = int(val)
        elif choice == "proxy":
            val = questionary.text(
                "代理地址 (如 socks5://127.0.0.1:1080，留空清除):",
                default=cfg.get("proxy", ""),
            ).ask()
            cfg["proxy"] = val.strip() if val else ""
        elif choice == "cookie":
            val = questionary.text(
                "Cookie 文件路径 (Netscape 格式，留空清除):",
                default=cfg.get("cookie_file", ""),
            ).ask()
            cfg["cookie_file"] = val.strip() if val else ""
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
            val = questionary.text(
                "字幕语言 (逗号分隔，如 zh,en):",
                default=cfg.get("subtitle_lang", "zh,en"),
            ).ask()
            if val is not None:
                cfg["subtitle_lang"] = val
        elif choice == "thumbnail":
            cfg["embed_thumbnail"] = questionary.confirm(
                "下载后嵌入缩略图？",
                default=cfg.get("embed_thumbnail", True),
            ).ask()
        elif choice == "concurrent":
            val = questionary.text(
                "并发下载数:", default=str(cfg.get("concurrent", 3))
            ).ask()
            if val and val.isdigit() and 1 <= int(val) <= 10:
                cfg["concurrent"] = int(val)
        elif choice == "retries":
            val = questionary.text(
                "最大重试次数:", default=str(cfg.get("max_retries", 3))
            ).ask()
            if val and val.isdigit():
                cfg["max_retries"] = int(val)
        elif choice == "continue":
            cfg["continue_dl"] = questionary.confirm(
                "启用断点续传？", default=cfg.get("continue_dl", True)
            ).ask()
        elif choice == "browser_headless":
            cfg["browser_headless"] = questionary.confirm(
                "浏览器使用无头模式？", default=cfg.get("browser_headless", True)
            ).ask()
        elif choice == "crawler_max_pages":
            val = questionary.text(
                "爬虫最大页数:", default=str(cfg.get("crawler_max_pages", 50))
            ).ask()
            if val and val.isdigit() and int(val) > 0:
                cfg["crawler_max_pages"] = int(val)
        elif choice == "crawler_max_depth":
            val = questionary.text(
                "爬虫最大深度:", default=str(cfg.get("crawler_max_depth", 3))
            ).ask()
            if val and val.isdigit() and int(val) > 0:
                cfg["crawler_max_depth"] = int(val)
