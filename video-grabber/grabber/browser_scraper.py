"""Browser-based scraper using Playwright.

Handles JS-rendered pages that yt-dlp and requests+BS4 can't reach.
Intercepts network traffic to discover video URLs mid-flight, and
extracts video elements from the fully-rendered DOM.

Usage:
    from grabber.browser_scraper import scrape_with_browser
    result = await scrape_with_browser("https://example.com/video-page")
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import urljoin

# ── constants ──────────────────────────────────────────────────────────────

VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".webm", ".m3u8", ".flv", ".mov",
    ".avi", ".wmv", ".ts", ".mpd", ".ogg", ".ogv",
)

VIDEO_CONTENT_TYPES = (
    "video/", "application/x-mpegURL", "application/vnd.apple.mpegurl",
    "application/dash+xml", "audio/",
)

SCRIPT_INJECT = """
// Before page load: intercept fetch/XHR to capture video URLs
(() => {
    window.__videoGrabber_urls = [];
    const origFetch = window.fetch;
    window.fetch = (...args) => {
        const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
        if (url) window.__videoGrabber_urls.push(url);
        return origFetch.apply(window, args);
    };
    const origXHR = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        this.__vg_url = url;
        return origXHR.apply(this, arguments);
    };
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function() {
        if (this.__vg_url) window.__videoGrabber_urls.push(this.__vg_url);
        return origSend.apply(this, arguments);
    };
})();
"""


# ── public API ─────────────────────────────────────────────────────────────

async def scrape_with_browser(
    url: str,
    *,
    headless: bool = True,
    timeout_ms: int = 30000,
    wait_until: str = "networkidle",
    scroll_to_bottom: bool = True,
    proxy: str = "",
) -> dict:
    """Use Playwright to load a page and extract video sources.

    Returns:
        {
            "page_title": str,
            "page_url": str,
            "network_videos": [str, ...],   # URLs intercepted from network
            "dom_videos":    [{url, type, ext, label}, ...],
            "dom_audio":     [{url, type, ext, label}, ...],
        }
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return _error("Playwright 未安装。运行: pip install playwright && playwright install chromium")

    result = {
        "page_title": "",
        "page_url": url,
        "network_videos": [],
        "dom_videos": [],
        "dom_audio": [],
    }

    async with async_playwright() as pw:
        launch_opts: dict = {"headless": headless}
        if proxy:
            launch_opts["proxy"] = {"server": proxy}

        browser = await pw.chromium.launch(**launch_opts)

        try:
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            # inject fetch/XHR interceptor before navigation
            await page.add_init_script(SCRIPT_INJECT)

            # navigate
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)

            # scroll to trigger lazy-loaded content
            if scroll_to_bottom:
                await page.evaluate("""
                    async () => {
                        for (let i = 0; i < 5; i++) {
                            window.scrollBy(0, window.innerHeight);
                            await new Promise(r => setTimeout(r, 500));
                        }
                    }
                """)

            result["page_title"] = await page.title()
            result["page_url"] = page.url  # after redirects

            # collect intercepted URLs
            network_urls: list[str] = await page.evaluate(
                "() => window.__videoGrabber_urls || []"
            )
            for u in network_urls:
                if _looks_like_video_url(u) and u not in result["network_videos"]:
                    result["network_videos"].append(u)

            # extract video/audio from DOM
            elements = await page.evaluate("""
                () => {
                    const videos = [], audios = [];

                    document.querySelectorAll('video, audio').forEach(el => {
                        const src = el.src || el.getAttribute('data-src') || '';
                        const poster = el.getAttribute('poster') || '';
                        const list = el.tagName === 'VIDEO' ? videos : audios;
                        if (src) list.push({url: src, type: el.tagName.toLowerCase() + '_tag'});
                        el.querySelectorAll('source').forEach(s => {
                            const surl = s.src || s.getAttribute('data-src') || '';
                            if (surl) list.push({url: surl, type: 'source_tag'});
                        });
                        if (poster) list.push({url: poster, type: 'poster'});
                    });

                    // also check for iframes to video platforms
                    document.querySelectorAll('iframe').forEach(el => {
                        const src = el.src || '';
                        if (src) videos.push({url: src, type: 'iframe'});
                    });

                    return {videos, audios};
                }
            """)

            for v in elements.get("videos", []):
                abs_u = urljoin(result["page_url"], v["url"])
                result["dom_videos"].append({
                    "url": abs_u,
                    "type": v["type"],
                    "ext": _guess_ext(abs_u),
                    "label": _label_for(v["type"]),
                })

            for a in elements.get("audios", []):
                abs_u = urljoin(result["page_url"], a["url"])
                result["dom_audio"].append({
                    "url": abs_u,
                    "type": a["type"],
                    "ext": _guess_ext(abs_u),
                    "label": _label_for(a["type"]),
                })

        finally:
            await browser.close()

    return result


def scrape_sync(url: str, **kwargs) -> dict:
    """Synchronous wrapper for scrape_with_browser."""
    return asyncio.run(scrape_with_browser(url, **kwargs))


# ── helpers ────────────────────────────────────────────────────────────────

def _looks_like_video_url(url: str) -> bool:
    lower = url.lower()
    for ext in VIDEO_EXTENSIONS:
        if ext in lower:
            return True
    if "m3u8" in lower or "mpd" in lower:
        return True
    return False


def _guess_ext(url: str) -> str:
    clean = url.split("?")[0].split("#")[0]
    ext = clean.rsplit(".", 1)[-1].lower()
    if ext in ("mp4", "mkv", "webm", "m3u8", "flv", "mov", "avi", "wmv", "ts", "mpd", "ogg", "ogv"):
        return ext
    if "m3u8" in clean.lower():
        return "m3u8"
    return ""


def _label_for(tag_type: str) -> str:
    labels = {
        "video_tag": "浏览器video标签",
        "audio_tag": "浏览器audio标签",
        "source_tag": "浏览器source标签",
        "iframe": "iframe嵌入",
        "poster": "视频封面",
    }
    return labels.get(tag_type, tag_type)


def _error(msg: str) -> dict:
    return {"page_title": "", "page_url": "", "error": msg,
            "network_videos": [], "dom_videos": [], "dom_audio": []}
