from yt_dlp import YoutubeDL

from ..utils.formatter import parse_size, format_duration


def extract_info(url: str, **opts) -> dict | None:
    """Extract video info using yt-dlp. Returns None on failure."""
    default = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
    default.update(opts)
    try:
        with YoutubeDL(default) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        return None


def list_formats(url: str, **extra_opts) -> list[dict]:
    """Return a list of available formats for the given URL."""
    info = extract_info(url, **extra_opts)
    if not info:
        return []

    formats = []
    for f in (info.get("formats") or [info]):
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue
        formats.append({
            "format_id": f.get("format_id", "?"),
            "ext": f.get("ext", "?"),
            "resolution": f.get("resolution")
            or f"{f.get('width', '?')}x{f.get('height', '?')}",
            "filesize": parse_size(f.get("filesize") or f.get("filesize_approx")),
            "vcodec": f.get("vcodec", "?"),
            "acodec": f.get("acodec", "?"),
            "fps": f.get("fps", ""),
            "note": f.get("format_note", ""),
        })

    return formats


def get_metadata(url: str, **extra_opts) -> dict:
    """Get video metadata (title, duration, thumbnail, etc.)."""
    info = extract_info(url, **extra_opts)
    if not info:
        return {}
    return {
        "title": info.get("title", "未知标题"),
        "duration": format_duration(info.get("duration")),
        "thumbnail": info.get("thumbnail", ""),
        "webpage_url": info.get("webpage_url", url),
        "uploader": info.get("uploader", "未知"),
        "description": (info.get("description") or "")[:200],
    }


def download_url(url: str, output_dir: str, format_spec: str = "best",
                 proxy: str = "", cookie_file: str = "",
                 progress_hooks: list | None = None) -> bool:
    """Download a video using yt-dlp. Returns True on success."""
    opts = {
        "outtmpl": f"{output_dir}/%(title)s.%(ext)s",
        "format": format_spec,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    if proxy:
        opts["proxy"] = proxy
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if progress_hooks:
        opts["progress_hooks"] = progress_hooks

    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
        return True
    except Exception:
        return False
