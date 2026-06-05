import re


def parse_size(text: str | None) -> int | None:
    """Convert size string like '245.3 MiB' to bytes."""
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*(K|M|G)i?B", text, re.I)
    if not match:
        return None
    num = float(match.group(1))
    unit = match.group(2).upper()
    multiplier = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}
    return int(num * multiplier.get(unit, 1))


def format_size(bytes_: int | None) -> str:
    if bytes_ is None:
        return "未知"
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024:
            return f"{bytes_:.1f} {unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f} TB"


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "未知"
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()
