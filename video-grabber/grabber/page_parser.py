"""Fallback parser for sites not supported by yt-dlp."""

import re

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def parse_page(url: str, timeout: int = 15) -> list[dict]:
    """Parse a generic HTML page for video sources."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    videos = []

    # 1. <video> tags
    for tag in soup.find_all("video"):
        src = tag.get("src")
        if src:
            videos.append({"src": src, "type": "video_tag", "ext": src.rsplit(".", 1)[-1]})
        for source in tag.find_all("source"):
            s = source.get("src")
            if s:
                videos.append({"src": s, "type": "video_tag", "ext": s.rsplit(".", 1)[-1]})

    # 2. m3u8 links in script/data config
    for script in soup.find_all("script"):
        if script.string:
            found = re.findall(r'(https?://[^"\']+\.m3u8[^"\']*)', script.string)
            for m in found:
                videos.append({"src": m, "type": "m3u8", "ext": "m3u8"})

    # 3. JSON-LD with contentUrl
    for tag in soup.find_all("script", type="application/ld+json"):
        if tag.string:
            for match in re.finditer(r'"contentUrl"\s*:\s*"([^"]+)"', tag.string):
                videos.append({"src": match.group(1), "type": "ldjson", "ext": "mp4"})

    return videos
