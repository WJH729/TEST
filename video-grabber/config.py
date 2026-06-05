import json
import shutil
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CONFIG_FILE = DATA_DIR / "config.json"

# ── quality presets ────────────────────────────────────────────────────────

QUALITY_PRESETS = {
    "best":    {"label": "最高画质 (自动)", "height": None},
    "2160p":   {"label": "4K (2160p)",       "height": 2160},
    "1440p":   {"label": "2K (1440p)",       "height": 1440},
    "1080p":   {"label": "全高清 (1080p)",    "height": 1080},
    "720p":    {"label": "高清 (720p)",       "height": 720},
    "480p":    {"label": "标清 (480p)",       "height": 480},
    "360p":    {"label": "流畅 (360p)",       "height": 360},
}

AUDIO_FORMATS = ["mp3", "m4a", "opus", "wav", "flac"]
MERGE_FORMATS = ["mp4", "mkv", "webm"]

DEFAULT_CONFIG = {
    # paths
    "output_dir": str(Path.home() / "Downloads" / "video-grabber"),
    "ffmpeg_path": "",
    "cookie_file": "",

    # quality
    "preferred_quality": "best",
    "max_filesize_mb": 0,       # 0 = no limit

    # format
    "merge_format": "mp4",
    "audio_format": "mp3",

    # subtitles
    "subtitle_lang": "zh,en",
    "download_subs": False,
    "embed_subs": False,

    # metadata
    "embed_thumbnail": True,
    "write_metadata": True,

    # network
    "proxy": "",
    "concurrent": 3,
    "max_retries": 3,
    "continue_dl": True,
}


def find_ffmpeg() -> str:
    return shutil.which("ffmpeg") or ""


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, ValueError):
            saved = {}
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
