import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_CONFIG = {
    "output_dir": str(Path.home() / "Downloads" / "video-grabber"),
    "proxy": "",
    "user_agent": "",
    "cookie_file": "",
    "concurrent": 3,
    "default_format": "best",
    "auto_merge": True,
}
CONFIG_FILE = DATA_DIR / "config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text("utf-8"))}
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
