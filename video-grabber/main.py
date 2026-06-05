#!/usr/bin/env python3
"""Video Grabber - CLI tool for downloading web videos.

Usage:
    python main.py              # 交互模式
    python main.py <URL>        # 直接下载指定 URL
    python main.py --batch FILE # 批量下载
    python main.py --history    # 查看下载历史
    python main.py --config     # 修改配置
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cli.menu import main_menu
from config import load_config
from grabber.ytdlp_wrapper import download_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="网页视频抓取工具 - 支持 YouTube、B站、抖音等数百个网站",
    )
    parser.add_argument(
        "url", nargs="?", help="视频页面 URL（不提供则进入交互模式）"
    )
    parser.add_argument(
        "-o", "--output", help="输出目录", default=None,
    )
    parser.add_argument(
        "-f", "--format", help="视频格式 (best, worst, 或格式 ID)", default="best",
    )
    parser.add_argument(
        "--audio-only", action="store_true", help="仅下载音频",
    )
    parser.add_argument(
        "--batch", metavar="FILE", help="从文件批量下载（每行一个 URL）",
    )
    parser.add_argument(
        "--history", action="store_true", help="查看下载历史",
    )
    parser.add_argument(
        "--config", action="store_true", help="打开设置",
    )
    parser.add_argument(
        "--proxy", help="代理地址 (如 socks5://127.0.0.1:1080)",
    )
    parser.add_argument(
        "--cookie", help="Cookie 文件路径",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    cfg = load_config()

    # 快速命令
    if args.history:
        from utils.history import load_history
        from rich.table import Table
        from rich import print as rprint
        history = load_history()
        if not history:
            rprint("[yellow]暂无下载记录[/yellow]")
        else:
            table = Table(title="下载历史")
            table.add_column("时间")
            table.add_column("标题")
            table.add_column("状态")
            table.add_column("链接")
            for r in reversed(history[-30:]):
                table.add_row(r["time"], r["title"][:30], r["status"], r["url"][:50])
            rprint(table)
        return

    if args.config:
        from cli.menu import handle_settings
        handle_settings(cfg)
        return

    # 批量模式
    if args.batch:
        filepath = Path(args.batch)
        if not filepath.exists():
            print(f"错误: 文件不存在 - {args.batch}")
            sys.exit(1)
        urls = [line.strip() for line in filepath.read_text("utf-8").splitlines() if line.strip()]
        output_dir = args.output or cfg["output_dir"]
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        success = 0
        for url in urls:
            print(f"正在下载: {url}")
            fmt = "bestaudio/best" if args.audio_only else args.format
            ok = download_url(
                url, output_dir, format_spec=fmt,
                proxy=args.proxy or cfg.get("proxy", ""),
                cookie_file=args.cookie or cfg.get("cookie_file", ""),
            )
            if ok:
                success += 1
        print(f"完成: {success}/{len(urls)} 成功")
        return

    # 单 URL 直接下载模式
    if args.url:
        output_dir = args.output or cfg["output_dir"]
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        fmt = "bestaudio/best" if args.audio_only else args.format
        print(f"正在下载: {args.url}")
        ok = download_url(
            args.url, output_dir, format_spec=fmt,
            proxy=args.proxy or cfg.get("proxy", ""),
            cookie_file=args.cookie or cfg.get("cookie_file", ""),
        )
        if ok:
            print("下载完成!")
        else:
            print("下载失败")
            sys.exit(1)
        return

    # 交互模式
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n  用户取消")
    except Exception as e:
        print(f"\n  发生错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
