from yt_dlp import YoutubeDL

from utils.formatter import parse_size, format_duration
from utils.history import add_record


def extract_info(url: str, **opts) -> dict | None:
    """Extract video/playlist info via yt-dlp. Returns None on failure."""
    default = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "ignoreerrors": True,
    }
    default.update(opts)
    try:
        with YoutubeDL(default) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None


def list_formats(url: str, **extra_opts) -> list[dict]:
    """Return available formats for the given URL."""
    info = extract_info(url, **extra_opts)
    if not info:
        return []

    raw_formats = info.get("formats") or []
    if not raw_formats:
        return []

    formats = []
    for f in raw_formats:
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue
        formats.append({
            "format_id": f.get("format_id", "?"),
            "ext": f.get("ext", "?"),
            "resolution": f.get("resolution")
            or f"{f.get('width', '?')}x{f.get('height', '?')}",
            "filesize": parse_size(
                f.get("filesize") or f.get("filesize_approx")
            ),
            "vcodec": f.get("vcodec", "?"),
            "acodec": f.get("acodec", "?"),
            "fps": f.get("fps"),
            "note": f.get("format_note", ""),
            "tbr": f.get("tbr"),
        })

    # sort by quality descending
    formats.sort(key=lambda x: x.get("tbr") or 0, reverse=True)
    return formats


def get_metadata(url: str, **extra_opts) -> dict:
    """Get video metadata: title, duration, uploader, thumbnail, etc."""
    info = extract_info(url, **extra_opts)
    if not info:
        return {}
    return {
        "title": info.get("title", ""),
        "duration": format_duration(info.get("duration")),
        "duration_sec": info.get("duration"),
        "thumbnail": info.get("thumbnail", ""),
        "webpage_url": info.get("webpage_url", url),
        "uploader": info.get("uploader", ""),
        "description": (info.get("description") or "")[:200],
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
    }


def get_playlist_entries(url: str, **extra_opts) -> list[dict]:
    """Extract playlist entries. Returns list of {title, url, duration}."""
    info = extract_info(url, **extra_opts)
    if not info:
        return []
    entries = info.get("entries") or []
    if not entries:
        return []
    result = []
    for entry in entries:
        if entry:
            result.append({
                "title": entry.get("title", "未知"),
                "url": entry.get("webpage_url") or entry.get("url", ""),
                "duration": format_duration(entry.get("duration")),
            })
    return result


def _make_progress_hook(rich_progress, task_id):
    """Create a closure-based progress hook that updates the matching rich task."""
    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                rich_progress.update(task_id, total=total, completed=downloaded)
        elif d["status"] == "finished":
            rich_progress.update(task_id, completed=1, total=1)
    return hook


def download_url(
    url: str,
    output_dir: str,
    format_spec: str = "bestvideo+bestaudio/best",
    audio_only: bool = False,
    subtitle_lang: str = "",
    proxy: str = "",
    cookie_file: str = "",
    merge_format: str = "mp4",
    max_retries: int = 3,
    continue_dl: bool = True,
    embed_thumbnail: bool = True,
    write_metadata: bool = True,
    ffmpeg_path: str = "",
    progress_hooks: list | None = None,
    audio_format: str = "mp3",
) -> bool:
    """Download a video using yt-dlp. Returns True on success."""
    if audio_only:
        tmpl = f"{output_dir}/%(title)s.%(ext)s"
        opts = {
            "outtmpl": tmpl,
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "continuedl": continue_dl,
            "retries": max_retries,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                "preferredquality": "192",
            }],
        }
    else:
        tmpl = f"{output_dir}/%(title)s.%(ext)s"
        opts = {
            "outtmpl": tmpl,
            "format": format_spec,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "continuedl": continue_dl,
            "retries": max_retries,
            "merge_output_format": merge_format,
            "postprocessors": [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": merge_format,
            }] if merge_format else [],
        }

    if embed_thumbnail:
        opts.setdefault("postprocessors", []).append({"key": "EmbedThumbnail"})
    if write_metadata:
        opts["postprocessors"] = opts.get("postprocessors", []) + [{"key": "FFmpegMetadata"}]

    if proxy:
        opts["proxy"] = proxy
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if subtitle_lang:
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = [s.strip() for s in subtitle_lang.split(",")]
    if progress_hooks:
        opts["progress_hooks"] = progress_hooks
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path

    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
        return True
    except Exception:
        return False


def download_playlist(
    playlist_url: str,
    output_dir: str,
    **kwargs,
) -> tuple[int, int]:
    """Download an entire playlist. Returns (success_count, total_count)."""
    info = extract_info(playlist_url)
    if not info:
        return 0, 0

    entries = info.get("entries") or []
    if not entries:
        return 0, 0

    success = 0
    total = len(entries)
    for entry in entries:
        if not entry:
            continue
        entry_url = entry.get("webpage_url") or entry.get("url") or entry.get("original_url", "")
        if not entry_url:
            continue
        ok = download_url(entry_url, output_dir, **kwargs)
        if ok:
            success += 1

    return success, total
