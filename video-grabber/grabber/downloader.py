"""Download engine supporting yt-dlp and direct HTTP downloads."""

import asyncio
from pathlib import Path

import aiofiles
import aiohttp
from aiohttp import ClientTimeout

from ..utils.formatter import safe_filename


async def download_file(url: str, dest: str, semaphore: asyncio.Semaphore,
                        session: aiohttp.ClientSession,
                        progress_cb=None) -> bool:
    """Download a single file chunk by chunk with progress callback."""
    async with semaphore:
        try:
            async with session.get(url, timeout=ClientTimeout(total=600)) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            progress_cb(downloaded, total)
                return True
        except Exception:
            return False


async def download_multiple(urls: list[tuple[str, str]],
                            max_concurrent: int = 3,
                            progress_cb=None) -> list[bool]:
    """Download multiple files concurrently."""
    semaphore = asyncio.Semaphore(max_concurrent)
    timeout = ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            download_file(url, safe_filename(dest), semaphore, session, progress_cb)
            for url, dest in urls
        ]
        return await asyncio.gather(*tasks)


def run_async_download(urls: list[tuple[str, str]], max_concurrent: int = 3,
                       progress_cb=None) -> list[bool]:
    """Synchronous entry point for async downloads."""
    return asyncio.run(download_multiple(urls, max_concurrent, progress_cb))
