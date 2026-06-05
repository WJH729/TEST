"""Fallback parser for sites not supported by yt-dlp.

Scans HTML for <video> tags, m3u8 links, mp4/mkv/webm in script data,
and JSON-LD structured data.
"""

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

VIDEO_EXT_PATTERN = re.compile(
    r'(https?://[^\s"\'<>]+\.(?:mp4|mkv|webm|m3u8|flv|mov|avi|wmv)(?:\?[^\s"\'<>]*)?)',
    re.I,
)


def parse_page(url: str, timeout: int = 15) -> list[dict]:
    """Parse a generic HTML page for video sources."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    html = resp.text
    soup = BeautifulSoup(html, "lxml")
    videos: list[dict] = []

    # 1. <video> tags and <source> children
    for tag in soup.find_all("video"):
        src = tag.get("src")
        if src:
            abs_src = urljoin(url, src)
            videos.append({
                "src": abs_src,
                "type": "video_tag",
                "ext": _guess_ext(abs_src),
            })
        for source in tag.find_all("source"):
            s = source.get("src")
            if s:
                abs_s = urljoin(url, s)
                videos.append({
                    "src": abs_s,
                    "type": "video_source",
                    "ext": _guess_ext(abs_s),
                })

    # 2. Direct video URLs in script / text (m3u8, mp4, etc.)
    for script in soup.find_all("script"):
        if script.string:
            for match in VIDEO_EXT_PATTERN.finditer(script.string):
                videos.append({
                    "src": match.group(1),
                    "type": "script_data",
                    "ext": _guess_ext(match.group(1)),
                })

    # 3. JSON-LD structured data
    for tag in soup.find_all("script", type="application/ld+json"):
        if tag.string:
            for m in re.finditer(r'"contentUrl"\s*:\s*"([^"]+)"', tag.string):
                videos.append({
                    "src": m.group(1),
                    "type": "jsonld",
                    "ext": _guess_ext(m.group(1)),
                })

    # 4. iframe embeds (extract src for follow-up)
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if src and any(d in src for d in ("youtube", "bilibili", "vimeo", "dailymotion", "youku")):
            abs_src = urljoin(url, src)
            videos.append({
                "src": abs_src,
                "type": "iframe_embed",
                "ext": "html",
            })

    return videos


def extract_page_metadata(url: str, timeout: int = 15) -> dict:
    """Extract basic metadata from an HTML page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return {}

    soup = BeautifulSoup(resp.text, "lxml")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # og:title
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"]

    description = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        description = og_desc["content"]

    thumbnail = ""
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        thumbnail = urljoin(url, og_img["content"])

    return {
        "title": title or "未知标题",
        "description": description[:200],
        "thumbnail": thumbnail,
        "webpage_url": url,
    }


def _guess_ext(url: str) -> str:
    """Guess file extension from a URL."""
    url_clean = url.split("?")[0]
    ext = url_clean.rsplit(".", 1)[-1].lower()
    if ext in ("mp4", "mkv", "webm", "m3u8", "flv", "mov", "avi", "wmv"):
        return ext
    if "m3u8" in url_clean:
        return "m3u8"
    return "mp4"
