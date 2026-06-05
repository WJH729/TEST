import json
import shutil
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CONFIG_FILE = DATA_DIR / "config.json"

DEFAULT_CONFIG = {
    "output_dir": str(Path.home() / "Downloads" / "video-grabber"),
    "proxy": "",
    "cookie_file": "",
    "concurrent": 3,
    "default_format": "bestvideo+bestaudio/best",
    "audio_format": "mp3",
    "merge_format": "mp4",
    "subtitle_lang": "zh,en",
    "download_subs": False,
    "embed_subs": False,
    "embed_thumbnail": True,
    "write_metadata": True,
    "max_retries": 3,
    "continue_dl": True,
    "ffmpeg_path": "",
}


def find_ffmpeg() -> str:
    """Try to find ffmpeg on the system."""
    found = shutil.which("ffmpeg")
    return found or ""


def load_config() -> dict:
    if CONFIG_FILE.exists():
        saved = json.loads(CONFIG_FILE.read_text("utf-8"))
        cfg = {**DEFAULT_CONFIG, **saved}
    else:
        cfg = dict(DEFAULT_CONFIG)
    if not cfg.get("ffmpeg_path"):
        cfg["ffmpeg_path"] = find_ffmpeg()
    return cfg


def save_config(cfg: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8"
    )
