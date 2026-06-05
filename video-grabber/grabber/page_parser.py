"""Enhanced page parser — discovers real video URLs embedded in any web page.

Strategy (layered, from most reliable to most speculative):
  1. yt-dlp (handled upstream) — always tried first
  2. <video> / <source> / <audio> tags
  3. Open Graph / Twitter Card meta tags (og:video, twitter:player)
  4. JSON-LD structured data (contentUrl, embedUrl)
  5. JavaScript player configs: `player.src=`, `videoUrl=`, `"url":`, `"src":`
  6. Direct video/m3u8 URLs anywhere in script text
  7. Common CDN / video-host URL heuristics
  8. iframe embeds → recursively parse one level deep
  9. Link tags with video-related type/href
"""

import json as json_lib
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ── constants ──────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

VIDEO_EXTENSIONS = {
    "mp4", "mkv", "webm", "m3u8", "flv", "mov", "avi",
    "wmv", "ts", "mpd", "ogg", "ogv",
}

# Sites yt-dlp can handle — if we find an iframe to these, just pass the iframe URL
YTDLP_SITES = (
    "youtube.com", "youtu.be",
    "bilibili.com",
    "vimeo.com",
    "dailymotion.com",
    "youku.com",
    "twitch.tv",
    "tiktok.com",
    "twitter.com", "x.com",
    "facebook.com", "fb.watch",
    "instagram.com",
    "nicovideo.jp",
    "iqiyi.com",
    "qq.com",  # 腾讯视频
    "weibo.com",
    "douyin.com",
)

# Common video CDN / hosting patterns for fuzzy matching
VIDEO_CDN_PATTERNS = (
    r"(?:cdn|video|media|stream|vod|live|hls)\.\w+\.\w+/",
    r"/video(s)?/",
    r"/media/",
    r"/vod/",
    r"/hls/",
    r"/stream(s)?/",
)


# ── regex helpers ──────────────────────────────────────────────────────────

def _build_video_url_re() -> re.Pattern:
    """Build a regex that finds any http(s) URL ending in a video extension."""
    exts = "|".join(VIDEO_EXTENSIONS)
    return re.compile(
        rf'(https?://[^\s"\'<>]+\.(?:{exts})(?:\?[^\s"\'<>]*)?)',
        re.IGNORECASE,
    )


_VIDEO_URL_RE = _build_video_url_re()

# Broad URL finder (any http URL inside quotes) — for script-data mining
_ANY_URL_RE = re.compile(r"""['"](https?://[^'"]+)['"]""", re.I)

# Key-based JSON patterns: "url", "src", "videoUrl", "video_url", "file", "source"
_KEY_URL_RE = re.compile(
    r"""['"](?:url|src|videoUrl|video_url|file|source|contentUrl|embedUrl|playUrl|streamUrl)['"]\s*:\s*['"](https?://[^'"]+)['"]""",
    re.I,
)

# JS assignment patterns
_JS_ASSIGN_RE = re.compile(
    r"""(?:var|let|const)\s+\w*(?:video|player|src|url)\w*\s*=\s*['"](https?://[^'"]+)['"]""",
    re.I,
)


# ── public API ─────────────────────────────────────────────────────────────

def discover_from_url(
    url: str,
    timeout: int = 15,
    follow_iframes: bool = True,
    max_iframes: int = 3,
) -> dict:
    """Main entry point: scan a page and return all discovered video info.

    Returns:
        {
            "page_title": str,
            "page_url": str,
            "thumbnail": str | None,
            "videos": [
                {"url": str, "type": str, "ext": str, "label": str},
                ...
            ],
        }
    """
    result = {
        "page_title": "",
        "page_url": url,
        "thumbnail": None,
        "videos": [],
    }

    html, final_url = _fetch(url, timeout)
    if not html:
        return result

    soup = BeautifulSoup(html, "lxml")

    # metadata
    result["page_title"] = _extract_title(soup, url)
    result["thumbnail"] = _extract_thumbnail(soup, final_url)

    seen = set()

    # layer 1: HTML tags
    for v in _parse_video_tags(soup, final_url):
        if v["url"] not in seen:
            seen.add(v["url"])
            result["videos"].append(v)

    # layer 2: meta tags
    for v in _parse_meta_tags(soup, final_url):
        if v["url"] not in seen:
            seen.add(v["url"])
            result["videos"].append(v)

    # layer 3: JSON-LD
    for v in _parse_jsonld(soup, final_url):
        if v["url"] not in seen:
            seen.add(v["url"])
            result["videos"].append(v)

    # layer 4: script data — this is the heavy lifter
    for v in _parse_script_data(soup, final_url):
        if v["url"] not in seen:
            seen.add(v["url"])
            result["videos"].append(v)

    # layer 5: link tags
    for v in _parse_link_tags(soup, final_url):
        if v["url"] not in seen:
            seen.add(v["url"])
            result["videos"].append(v)

    # layer 6: iframes
    if follow_iframes:
        for v in _parse_iframes(soup, final_url, timeout, max_iframes):
            if v["url"] not in seen:
                seen.add(v["url"])
                result["videos"].append(v)

    return result


# ── layer parsers ──────────────────────────────────────────────────────────

def _fetch(url: str, timeout: int) -> tuple[str | None, str]:
    """Fetch a page, follow redirects. Returns (html, final_url)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        # only parse HTML responses
        ct = resp.headers.get("Content-Type", "")
        if "html" not in ct and "text" not in ct:
            return None, url
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text, resp.url
    except requests.RequestException:
        return None, url


def _parse_video_tags(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """<video>, <source>, <audio>, <embed>, <object> tags."""
    results = []
    for tag in soup.find_all(["video", "audio"]):
        tag_name = tag.name
        for attr in ("src", "data-src", "data-original"):
            src = tag.get(attr)
            if src:
                abs_url = urljoin(base_url, src)
                results.append(_make_entry(abs_url, f"{tag_name}_tag"))
        for source in tag.find_all("source"):
            for attr in ("src", "data-src"):
                s = source.get(attr)
                if s:
                    abs_url = urljoin(base_url, s)
                    results.append(_make_entry(abs_url, "source_tag"))
        # poster image can hint at video
        poster = tag.get("poster")
        if poster:
            results.append(_make_entry(urljoin(base_url, poster), "poster"))

    # <embed> and <object> tags (Flash-era fallback, sometimes still used)
    for tag in soup.find_all(["embed", "object"]):
        src = tag.get("src") or tag.get("data")
        if src:
            abs_url = urljoin(base_url, src)
            results.append(_make_entry(abs_url, tag.name + "_tag"))

    return results


def _parse_meta_tags(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Open Graph, Twitter Card meta tags."""
    results = []
    meta_props = [
        ("meta[property='og:video']", "content"),
        ("meta[property='og:video:url']", "content"),
        ("meta[property='og:video:secure_url']", "content"),
        ("meta[name='twitter:player']", "content"),
        ("meta[name='twitter:player:stream']", "content"),
        ("meta[property='og:audio']", "content"),
        ("meta[itemprop='contentUrl']", "content"),
    ]
    for selector, attr in meta_props:
        tag = soup.select_one(selector)
        if tag and tag.get(attr):
            abs_url = urljoin(base_url, tag[attr])
            results.append(_make_entry(abs_url, "meta_tag"))
    return results


def _parse_jsonld(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """JSON-LD structured data."""
    results = []
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string:
            continue
        try:
            data = json_lib.loads(tag.string)
        except (json_lib.JSONDecodeError, TypeError, ValueError):
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("contentUrl", "embedUrl", "encodingFormat"):
                val = item.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    results.append(_make_entry(val, "jsonld"))
            # VideoObject has a thumbnail too
            thumb = item.get("thumbnailUrl")
            if isinstance(thumb, str) and thumb.startswith("http"):
                results.append(_make_entry(thumb, "jsonld_thumb"))

    return results


def _parse_script_data(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Deep-scan all <script> blocks for video URLs."""
    results = []

    for script in soup.find_all("script"):
        if not script.string:
            continue
        text = script.string

        # A. key:value JSON-style assignments — most reliable
        for m in _KEY_URL_RE.finditer(text):
            raw = m.group(1)
            if _looks_like_video_url(raw):
                results.append(_make_entry(raw, "script_config"))

        # B. JS variable assignments
        for m in _JS_ASSIGN_RE.finditer(text):
            raw = m.group(1)
            if _looks_like_video_url(raw):
                results.append(_make_entry(raw, "script_assign"))

        # C. any URL with known video extensions
        for m in _VIDEO_URL_RE.finditer(text):
            raw = m.group(1)
            results.append(_make_entry(raw, "script_url"))
            if len(results) >= 50:
                break

        # D. try parsing as JSON
        results.extend(_scan_json_text(text, base_url))

    return results


def _scan_json_text(text: str, base_url: str) -> list[dict]:
    """Try to extract JSON objects from script text and scan for video keys."""
    results = []
    # find JSON-like objects
    for match in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text):
        try:
            obj = json_lib.loads(match.group())
        except (json_lib.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        for key, val in obj.items():
            if not isinstance(val, str):
                continue
            key_lower = key.lower()
            if any(kw in key_lower for kw in ("url", "src", "video", "file", "stream", "source", "play")):
                if val.startswith("http") and _looks_like_video_url(val):
                    results.append(_make_entry(val, f"json_{key}"))
    return results


def _parse_link_tags(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """<link> tags with video types, <a> tags linking to videos."""
    results = []
    for link in soup.find_all("link"):
        href = link.get("href", "")
        rel = (link.get("rel") or [])
        if isinstance(rel, str):
            rel = [rel]
        if any(r in ("video_src", "video") for r in rel) and href:
            results.append(_make_entry(urljoin(base_url, href), "link_tag"))

    # some pages have direct <a> links to video files
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if _looks_like_video_url(href):
            results.append(_make_entry(urljoin(base_url, href), "a_tag"))

    return results


def _parse_iframes(
    soup: BeautifulSoup, base_url: str, timeout: int, max_iframes: int
) -> list[dict]:
    """Follow iframes one level deep."""
    results = []
    iframes_followed = 0

    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if not src:
            continue
        abs_src = urljoin(base_url, src)

        # if it's a yt-dlp-supported site, pass the iframe URL directly
        if any(site in abs_src for site in YTDLP_SITES):
            results.append(_make_entry(abs_src, "iframe_embed"))
            continue

        # otherwise, try to fetch and parse the iframe
        if iframes_followed >= max_iframes:
            results.append(_make_entry(abs_src, "iframe_raw"))
            continue

        iframes_followed += 1
        discovery = discover_from_url(abs_src, timeout=timeout, follow_iframes=False)
        for v in discovery.get("videos", []):
            results.append(v)

    return results


# ── helpers ────────────────────────────────────────────────────────────────

def _looks_like_video_url(url: str) -> bool:
    """Heuristic: does this URL look like it points to a video resource?"""
    if not url or not url.startswith("http"):
        return False

    lower = url.lower()

    # direct video extension
    for ext in VIDEO_EXTENSIONS:
        if f".{ext}" in lower:
            return True

    # m3u8 without extension
    if "m3u8" in lower:
        return True

    # CDN heuristics (but only with ?v=... or similar)
    for pat in VIDEO_CDN_PATTERNS:
        if re.search(pat, lower):
            return True

    return False


def _make_entry(url: str, source_type: str) -> dict:
    return {
        "url": url,
        "type": source_type,
        "ext": _guess_ext(url),
        "label": _make_label(url, source_type),
    }


def _make_label(url: str, source_type: str) -> str:
    """Human-readable label for a discovered video."""
    type_labels = {
        "video_tag": "网页video标签",
        "audio_tag": "网页audio标签",
        "source_tag": "网页source标签",
        "embed_tag": "网页embed标签",
        "object_tag": "网页object标签",
        "meta_tag": "页面meta信息",
        "jsonld": "结构化数据",
        "jsonld_thumb": "缩略图",
        "script_config": "JS配置",
        "script_assign": "JS变量",
        "script_url": "脚本内URL",
        "iframe_embed": "iframe嵌入",
        "iframe_raw": "iframe来源",
        "link_tag": "链接标签",
        "a_tag": "页面链接",
        "poster": "视频封面",
    }
    base = type_labels.get(source_type, source_type)
    ext = _guess_ext(url)
    if ext and ext != "mp4":
        base += f" ({ext.upper()})"
    return base


def _guess_ext(url: str) -> str:
    url_clean = url.split("?")[0].split("#")[0]
    ext = url_clean.rsplit(".", 1)[-1].lower()
    if ext in VIDEO_EXTENSIONS:
        return ext
    if "m3u8" in url_clean.lower():
        return "m3u8"
    return ""


def _extract_title(soup: BeautifulSoup, base_url: str) -> str:
    """Best-effort page title."""
    # og:title
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    # <title>
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return "未知标题"


def _extract_thumbnail(soup: BeautifulSoup, base_url: str) -> str | None:
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return urljoin(base_url, og["content"])
    return None


# ── legacy aliases ─────────────────────────────────────────────────────────

def parse_page(url: str, timeout: int = 15) -> list[dict]:
    """Legacy alias — returns flat video list."""
    discovery = discover_from_url(url, timeout=timeout)
    return discovery.get("videos", [])


def extract_page_metadata(url: str, timeout: int = 15) -> dict:
    """Legacy alias — returns metadata only."""
    discovery = discover_from_url(url, timeout=timeout)
    return {
        "title": discovery["page_title"],
        "description": "",
        "thumbnail": discovery["thumbnail"] or "",
        "webpage_url": discovery["page_url"],
    }
