"""Site crawler — recursively crawl a website to discover video pages.

Strategies:
  1. Sitemap.xml parsing — fast discovery of video URLs
  2. Breadth-first crawl — follow internal links N levels deep
  3. Focused crawl — prioritize pages with video-like URL patterns

Usage:
    from grabber.site_crawler import crawl_site
    results = crawl_site("https://example.com", max_pages=50, max_depth=3)
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

import aiohttp
from aiohttp import ClientTimeout

from grabber.page_parser import discover_from_url

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# URL patterns that are likely to contain video
VIDEO_PATH_PATTERNS = (
    r"/video(s?)/",
    r"/watch",
    r"/embed/",
    r"/play/",
    r"/media/",
    r"/tv/",
    r"/movie(s?)/",
    r"/clip(s?)/",
    r"/vod/",
    r"/live/",
    r"/stream(s?)/",
)

# Patterns to SKIP (non-content pages)
SKIP_PATTERNS = (
    r"/login", r"/signup", r"/register", r"/logout",
    r"/cart", r"/checkout", r"/account", r"/profile",
    r"/search", r"/tag/", r"/category/", r"/author/",
    r"/page/\d+", r"/comment", r"/reply",
    r"/css/", r"/js/", r"/img/", r"/image/", r"/asset/",
    r"\.pdf$", r"\.jpg$", r"\.png$", r"\.gif$", r"\.css$", r"\.js$",
    r"#", r"\?share=", r"\?replytocom=",
)


@dataclass
class CrawlResult:
    url: str
    title: str = ""
    video_count: int = 0
    videos: list[dict] = field(default_factory=list)
    depth: int = 0


@dataclass
class CrawlConfig:
    max_pages: int = 50
    max_depth: int = 3
    timeout: int = 15
    concurrency: int = 5
    same_domain: bool = True
    follow_subdomains: bool = False
    respect_robots: bool = True
    priority_video_paths: bool = True
    proxy: str = ""


# ── sitemap discovery ──────────────────────────────────────────────────────

def parse_sitemap(url_or_domain: str, timeout: int = 15) -> list[str]:
    """Discover video-related URLs from sitemap.xml.

    Tries common sitemap locations:
      - /sitemap.xml
      - /sitemap_index.xml
      - /sitemap-video.xml
    """
    base = _ensure_base(url_or_domain)
    urls: list[str] = []
    seen = set()

    sitemap_urls = [
        urljoin(base, "sitemap.xml"),
        urljoin(base, "sitemap_index.xml"),
        urljoin(base, "sitemap-video.xml"),
    ]

    for sm_url in sitemap_urls:
        if sm_url in seen:
            continue
        seen.add(sm_url)
        xml_text = _fetch_sync(sm_url, timeout)
        if not xml_text:
            continue

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            continue

        ns_sm = "http://www.sitemaps.org/schemas/sitemap/0.9"
        ns_video = "http://www.google.com/schemas/sitemap-video/1.1"

        # check if it's a sitemap index (points to other sitemaps)
        for sm in root.findall(f"{{{ns_sm}}}sitemap"):
            loc = sm.findtext(f"{{{ns_sm}}}loc")
            if loc and loc not in seen:
                seen.add(loc)
                urls.extend(parse_sitemap(loc, timeout))

        # extract URLs from <url> elements
        for url_el in root.findall(f"{{{ns_sm}}}url"):
            loc = url_el.findtext(f"{{{ns_sm}}}loc")
            if loc:
                urls.append(loc)

            # check for video sitemap extension
            for vid in url_el.findall(f"{{{ns_video}}}video"):
                content_loc = vid.findtext(f"{{{ns_video}}}content_loc")
                player_loc = vid.findtext(f"{{{ns_video}}}player_loc")
                if content_loc:
                    urls.append(content_loc)
                if player_loc:
                    urls.append(player_loc)

    return sorted(set(urls))


# ── site crawling ──────────────────────────────────────────────────────────

def crawl_site(
    start_url: str,
    max_pages: int = 50,
    max_depth: int = 3,
    timeout: int = 15,
    concurrency: int = 5,
    same_domain: bool = True,
    proxy: str = "",
) -> list[CrawlResult]:
    """Recursively crawl a site to find pages with video content."""
    return asyncio.run(_crawl_async(
        start_url,
        CrawlConfig(
            max_pages=max_pages,
            max_depth=max_depth,
            timeout=timeout,
            concurrency=concurrency,
            same_domain=same_domain,
            proxy=proxy,
        ),
    ))


async def _crawl_async(start_url: str, cfg: CrawlConfig) -> list[CrawlResult]:
    base_domain = urlparse(start_url).netloc.lower()

    # fetch & respect robots.txt
    robot = RobotFileParser()
    if cfg.respect_robots:
        try:
            robots_url = urljoin(_ensure_base(start_url), "/robots.txt")
            robot.set_url(robots_url)
            robot.read()
        except Exception:
            pass

    queue: deque[tuple[str, int]] = deque()
    queue.append((start_url, 0))

    visited: set[str] = set()
    results: list[CrawlResult] = []
    sem = asyncio.Semaphore(cfg.concurrency)
    timeout_obj = ClientTimeout(total=cfg.timeout)

    async def _fetch_page(session: aiohttp.ClientSession, url: str) -> str | None:
        try:
            async with session.get(url, headers=HEADERS, timeout=timeout_obj) as resp:
                if resp.status != 200:
                    return None
                ct = resp.headers.get("Content-Type", "")
                if "html" not in ct and "text" not in ct:
                    return None
                return await resp.text()
        except Exception:
            return None

    async def _worker(session: aiohttp.ClientSession):
        while queue and len(visited) < cfg.max_pages:
            try:
                url, depth = queue.popleft()
            except IndexError:
                return

            if url in visited:
                continue
            if depth > cfg.max_depth:
                continue

            # robots check
            if cfg.respect_robots:
                try:
                    if not robot.can_fetch(HEADERS["User-Agent"], url):
                        continue
                except Exception:
                    pass

            visited.add(url)

            html = await _fetch_page(session, url)
            if not html:
                continue

            # discover videos on this page
            discover = discover_from_url(url, timeout=cfg.timeout, follow_iframes=False)
            videos = discover.get("videos", [])

            if videos:
                results.append(CrawlResult(
                    url=url,
                    title=discover.get("page_title", ""),
                    video_count=len(videos),
                    videos=videos,
                    depth=depth,
                ))

            # extract links for further crawling (only if not at max depth)
            if depth < cfg.max_depth and len(visited) < cfg.max_pages:
                links = _extract_links(html, url, base_domain, cfg)
                for link in links:
                    if link not in visited:
                        queue.append((link, depth + 1))

    async with aiohttp.ClientSession() as session:
        workers = [_worker(session) for _ in range(cfg.concurrency)]
        await asyncio.gather(*workers)

    # sort: video pages first, then by depth
    results.sort(key=lambda r: (-r.video_count, r.depth))
    return results


# ── link extraction ────────────────────────────────────────────────────────

def _extract_links(html: str, base_url: str, base_domain: str,
                   cfg: CrawlConfig) -> list[str]:
    """Extract internal links from HTML, prioritizing video-like URLs."""
    links: list[str] = []
    seen = set()

    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1)
        abs_url = urljoin(base_url, href)

        if abs_url in seen:
            continue
        seen.add(abs_url)

        parsed = urlparse(abs_url)

        # scheme check
        if parsed.scheme not in ("http", "https"):
            continue

        # domain check
        domain = parsed.netloc.lower()
        if cfg.same_domain:
            if cfg.follow_subdomains:
                if not domain.endswith(base_domain.split(".", 1)[-1]):
                    continue
            else:
                if domain != base_domain:
                    continue

        # skip unwanted patterns
        path = parsed.path.lower()
        if any(re.search(p, path) for p in SKIP_PATTERNS):
            continue

        # skip fragments/queries that duplicate content
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if clean in seen:
            continue
        seen.add(clean)

        links.append(abs_url)

    # sort: video-like paths first (if priority enabled)
    if cfg.priority_video_paths:
        def _priority(link: str) -> int:
            path = urlparse(link).path.lower()
            for pat in VIDEO_PATH_PATTERNS:
                if re.search(pat, path):
                    return 0
            return 1
        links.sort(key=_priority)

    return links


# ── helpers ────────────────────────────────────────────────────────────────

def _ensure_base(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def _fetch_sync(url: str, timeout: int) -> str | None:
    import requests as req
    try:
        resp = req.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception:
        return None
