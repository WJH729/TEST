import json
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path(__file__).parent.parent / "data" / "history.json"


def load_history() -> list[dict]:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text("utf-8"))
    return []


def save_history(history: list[dict]):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history[-200:], ensure_ascii=False, indent=2), "utf-8"
    )


def add_record(url: str, title: str, filepath: str, status: str):
    history = load_history()
    history.append({
        "url": url,
        "title": title,
        "filepath": filepath,
        "status": status,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_history(history)
