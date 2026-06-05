"""Async download engine for direct HTTP downloads (non-yt-dlp paths)."""

import asyncio
from pathlib import Path

import aiohttp
from aiohttp import ClientTimeout

from utils.formatter import safe_filename


async def _download_one(
    url: str,
    dest: str,
    semaphore: asyncio.Semaphore,
    session: aiohttp.ClientSession,
    progress_cb=None,
    retries: int = 3,
) -> bool:
    """Download a single file with retry support."""
    async with semaphore:
        for attempt in range(retries):
            try:
                async with session.get(
                    url, timeout=ClientTimeout(total=600)
                ) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    Path(dest).parent.mkdir(parents=True, exist_ok=True)
                    with open(dest, "wb") as f:
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb:
                                progress_cb(downloaded, total)
                return True
            except Exception:
                if attempt == retries - 1:
                    return False
                await asyncio.sleep(1)
        return False


async def _download_multiple(
    urls: list[tuple[str, str]],
    max_concurrent: int = 3,
    progress_cb=None,
    retries: int = 3,
) -> list[bool]:
    """Download multiple files concurrently."""
    semaphore = asyncio.Semaphore(max_concurrent)
    timeout = ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            _download_one(u, safe_filename(d), semaphore, session, progress_cb, retries)
            for u, d in urls
        ]
        return await asyncio.gather(*tasks)


def run_async_download(
    urls: list[tuple[str, str]],
    max_concurrent: int = 3,
    progress_cb=None,
    retries: int = 3,
) -> list[bool]:
    """Sync entry point for async downloads."""
    return asyncio.run(
        _download_multiple(urls, max_concurrent, progress_cb, retries)
    )
