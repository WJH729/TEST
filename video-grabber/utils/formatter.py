import re
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════
#  File-size helpers
# ═══════════════════════════════════════════════════════════════════════════

def parse_size(text: str | None) -> int | None:
    """Convert size string like '245.3 MiB' to bytes."""
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*(K|M|G|T)i?B", text, re.I)
    if not match:
        return None
    num = float(match.group(1))
    unit = match.group(2).upper()
    multiplier = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
    return int(num * multiplier.get(unit, 1))


def format_size(bytes_: int | None) -> str:
    if bytes_ is None:
        return "未知"
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024:
            return f"{bytes_:.1f} {unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f} TB"


# ═══════════════════════════════════════════════════════════════════════════
#  Duration helpers
# ═══════════════════════════════════════════════════════════════════════════

def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "未知"
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ═══════════════════════════════════════════════════════════════════════════
#  Filename helpers
# ═══════════════════════════════════════════════════════════════════════════

def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


# ═══════════════════════════════════════════════════════════════════════════
#  Resolution / quality helpers
# ═══════════════════════════════════════════════════════════════════════════

# Mapping from quality preset keys to yt-dlp format selectors that
# pick the best stream ≤ the given height.
QUALITY_YTDLP_SELECTORS: dict[str, str] = {
    "best":   "bestvideo+bestaudio/best",
    "2160p":  "bestvideo[height<=2160]+bestaudio/best[height<=2160]",
    "1440p":  "bestvideo[height<=1440]+bestaudio/best[height<=1440]",
    "1080p":  "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p":   "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p":   "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "360p":   "bestvideo[height<=360]+bestaudio/best[height<=360]",
}

QUALITY_HEIGHTS: dict[str, int | None] = {
    "best":   None,
    "2160p":  2160,
    "1440p":  1440,
    "1080p":  1080,
    "720p":   720,
    "480p":   480,
    "360p":   360,
}


def parse_resolution(res: str | None) -> Optional[tuple[int, int]]:
    """Parse a resolution string like '1920x1080' → (1920, 1080).
    Returns None for unparseable input."""
    if not res:
        return None
    m = re.search(r"(\d{2,5})\s*[x×]\s*(\d{2,5})", res)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def extract_height(res: str | None) -> int | None:
    """Extract vertical resolution from a string like '1920x1080' → 1080."""
    parsed = parse_resolution(res)
    return parsed[1] if parsed else None


def quality_to_ytdlp_format(quality_key: str) -> str:
    """Map a quality preset key to a yt-dlp format selector string."""
    return QUALITY_YTDLP_SELECTORS.get(
        quality_key,
        QUALITY_YTDLP_SELECTORS["best"],
    )


def quality_to_max_height(quality_key: str) -> int | None:
    """Return the max height for a quality preset (None = no limit)."""
    return QUALITY_HEIGHTS.get(quality_key)


def sort_formats_by_quality(formats: list[dict]) -> list[dict]:
    """Sort formats by resolution height descending, best first."""
    def _key(f: dict) -> int:
        h = extract_height(f.get("resolution"))
        return h or 0
    return sorted(formats, key=_key, reverse=True)


def filter_formats_by_max_height(formats: list[dict], max_height: int | None) -> list[dict]:
    """Keep only formats ≤ max_height (None = keep all)."""
    if max_height is None:
        return formats
    return [
        f for f in formats
        if (extract_height(f.get("resolution")) or 9999) <= max_height
    ]


def best_format_at_height(formats: list[dict], target_height: int) -> dict | None:
    """Find best format whose height is ≤ target_height.
    Prefers highest resolution + largest file size."""
    filtered = filter_formats_by_max_height(formats, target_height)
    if not filtered:
        return None
    # sort by resolution desc, then by filesize desc (if available)
    def _key(f: dict) -> tuple[int, int]:
        h = extract_height(f.get("resolution")) or 0
        s = f.get("filesize") or 0
        return h, s
    return max(filtered, key=_key)


def get_quality_choices() -> list[str]:
    """Return ordered list of quality preset keys (best → lowest)."""
    return list(QUALITY_YTDLP_SELECTORS.keys())
