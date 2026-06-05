#!/usr/bin/env python3
"""Video Grabber - CLI tool for downloading web videos."""

import sys
from pathlib import Path

# ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from cli.menu import main_menu


def main():
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n[yellow]用户取消[/yellow]")
    except Exception as e:
        print(f"\n[red]发生错误: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
