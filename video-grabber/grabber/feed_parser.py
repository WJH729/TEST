"""RSS / Atom feed parser for video discovery.

Parses feed XML to find:
  - <enclosure> tags with video MIME types
  - <media:content> / <media:group> elements (Media RSS / MRSS)
  - <link> elements pointing to video pages
  - <content:encoded> HTML that may embed video players

Usage:
    from grabber.feed_parser import parse_feed_url
    videos = parse_feed_url("https://example.com/feed.xml")
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests

# ── namespaces ─────────────────────────────────────────────────────────────

NS = {
    "atom":     "http://www.w3.org/2005/Atom",
    "media":    "http://search.yahoo.com/mrss/",
    "content":  "http://purl.org/rss/1.0/modules/content/",
    "itunes":   "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "dc":       "http://purl.org/dc/elements/1.1/",
}

VIDEO_MIMES = {
    "video/mp4", "video/webm", "video/x-m4v", "video/quicktime",
    "video/x-msvideo", "video/x-ms-wmv", "video/ogg", "video/mpeg",
    "application/x-mpegURL", "application/vnd.apple.mpegurl",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


# ── public API ─────────────────────────────────────────────────────────────

def parse_feed_url(feed_url: str, timeout: int = 15) -> dict:
    """Fetch and parse a feed URL. Returns structured result.

    Returns:
        {
            "feed_title": str,
            "feed_url": str,
            "entries": [
                {
                    "title": str,
                    "url": str,            # link to original page/post
                    "published": str,
                    "videos": [{url, type, ext, mime, size}, ...],
                },
                ...
            ],
        }
    """
    result: dict = {"feed_title": "", "feed_url": feed_url, "entries": []}

    xml_text = _fetch(feed_url, timeout)
    if not xml_text:
        return result

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return result

    # feed title
    result["feed_title"] = _first_text(root, ".//title") or feed_url

    # detect feed type and parse entries
    if root.tag == "{http://www.w3.org/2005/Atom}feed":
        _parse_atom(root, result)
    elif root.tag == "rss":
        channel = root.find("channel")
        if channel is not None:
            _parse_rss(channel, result)
    else:
        # try both
        _parse_rss(root, result)
        _parse_atom(root, result)

    return result


# ── feed parsers ───────────────────────────────────────────────────────────

def _parse_rss(channel: ET.Element, result: dict):
    for item in channel.findall("item"):
        entry = _parse_rss_item(item)
        if entry:
            result["entries"].append(entry)


def _parse_rss_item(item: ET.Element) -> dict | None:
    title = _first_text(item, "title") or "无标题"
    link = _first_text(item, "link") or ""
    pub = _first_text(item, "pubDate") or ""

    videos: list[dict] = []

    # 1. <enclosure> — common for podcast/video RSS
    for enc in item.findall("enclosure"):
        url = enc.get("url", "")
        mime = enc.get("type", "")
        size = enc.get("length", "")
        if url and (_is_video_mime(mime) or _looks_like_video_url(url)):
            videos.append({
                "url": url,
                "type": "enclosure",
                "ext": _guess_ext(url),
                "mime": mime,
                "size": size,
            })

    # 2. <media:content> / <media:group> (Media RSS)
    for media in item.findall("media:content", NS):
        url = media.get("url", "")
        mime = media.get("type", "")
        if url and (_is_video_mime(mime) or _looks_like_video_url(url)):
            videos.append({
                "url": url,
                "type": "media:content",
                "ext": _guess_ext(url),
                "mime": mime,
                "size": media.get("fileSize", ""),
            })
    for media in item.findall("media:group", NS):
        for mc in media.findall("media:content", NS):
            url = mc.get("url", "")
            mime = mc.get("type", "")
            if url and (_is_video_mime(mime) or _looks_like_video_url(url)):
                videos.append({
                    "url": url,
                    "type": "media:content",
                    "ext": _guess_ext(url),
                    "mime": mime,
                    "size": mc.get("fileSize", ""),
                })

    # 3. <content:encoded> — scan embedded HTML for video URLs
    for encoded in item.findall("content:encoded", NS):
        text = encoded.text or ""
        for m in re.finditer(
            r'(https?://[^\s"\'<>]+\.(?:mp4|mkv|webm|m3u8|mov)(?:\?[^\s"\'<>]*)?)',
            text, re.I,
        ):
            videos.append({
                "url": m.group(1),
                "type": "content_html",
                "ext": _guess_ext(m.group(1)),
                "mime": "",
                "size": "",
            })

    if not videos and not link:
        return None

    return {
        "title": title,
        "url": link,
        "published": pub,
        "videos": videos,
    }


def _parse_atom(feed: ET.Element, result: dict):
    for entry in feed.findall("atom:entry", NS):
        e = _parse_atom_entry(entry)
        if e:
            result["entries"].append(e)


def _parse_atom_entry(entry: ET.Element) -> dict | None:
    title = _first_text(entry, "atom:title", NS) or "无标题"
    link = ""
    for lk in entry.findall("atom:link", NS):
        href = lk.get("href", "")
        rel = lk.get("rel", "alternate")
        mime = lk.get("type", "")
        if rel == "alternate":
            link = link or href
        if _is_video_mime(mime):
            link = link or href  # video link takes priority

    pub = (
        _first_text(entry, "atom:published", NS)
        or _first_text(entry, "atom:updated", NS)
        or ""
    )

    videos: list[dict] = []

    # check atom:link with video mime types
    for lk in entry.findall("atom:link", NS):
        href = lk.get("href", "")
        mime = lk.get("type", "")
        if href and _is_video_mime(mime):
            videos.append({
                "url": href,
                "type": "atom_link",
                "ext": _guess_ext(href),
                "mime": mime,
                "size": lk.get("length", ""),
            })

    # check atom:content for embedded video URLs
    for content in entry.findall("atom:content", NS):
        text = content.text or ""
        for m in re.finditer(
            r'(https?://[^\s"\'<>]+\.(?:mp4|mkv|webm|m3u8|mov)(?:\?[^\s"\'<>]*)?)',
            text, re.I,
        ):
            videos.append({
                "url": m.group(1),
                "type": "atom_content",
                "ext": _guess_ext(m.group(1)),
                "mime": "",
                "size": "",
            })

    return {
        "title": title,
        "url": link,
        "published": pub,
        "videos": videos,
    }


# ── helpers ────────────────────────────────────────────────────────────────

def _fetch(url: str, timeout: int) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.RequestException:
        return None


def _first_text(el: ET.Element, tag: str, ns: dict | None = None) -> str:
    child = el.find(tag, ns or {}) if ns else el.find(tag)
    if child is not None:
        return (child.text or "").strip()
    return ""


def _is_video_mime(mime: str) -> bool:
    if not mime:
        return False
    m = mime.lower()
    if m in VIDEO_MIMES:
        return True
    return m.startswith("video/") or m.startswith("audio/")


def _looks_like_video_url(url: str) -> bool:
    lower = url.lower()
    for ext in (".mp4", ".mkv", ".webm", ".m3u8", ".flv", ".mov", ".avi", ".wmv"):
        if ext in lower:
            return True
    return False


def _guess_ext(url: str) -> str:
    clean = url.split("?")[0].split("#")[0]
    ext = clean.rsplit(".", 1)[-1].lower()
    if ext in ("mp4", "mkv", "webm", "m3u8", "flv", "mov", "avi", "wmv"):
        return ext
    return ""
