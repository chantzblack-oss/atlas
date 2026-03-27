import json
import tempfile
from pathlib import Path

ATLAS_DIR = Path.home() / ".atlas"
HISTORY_FILE = ATLAS_DIR / "history.json"


def ensure_dir():
    ATLAS_DIR.mkdir(exist_ok=True)


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_exploration(exploration: dict):
    ensure_dir()
    history = load_history()
    history.append(exploration)
    # Atomic write: write to temp file then rename
    data = json.dumps(history, indent=2, ensure_ascii=False)
    tmp = ATLAS_DIR / ".history.tmp"
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(HISTORY_FILE)


def find_connections(new_tags: list[str], history: list[dict],
                     threshold: float = 0.15) -> list[str]:
    if not new_tags or not history:
        return []
    new_set = set(t.lower() for t in new_tags)
    connections = []
    for entry in history:
        old_tags = set(t.lower() for t in entry.get("tags", []))
        if not old_tags:
            continue
        jaccard = len(new_set & old_tags) / len(new_set | old_tags)
        if jaccard >= threshold:
            connections.append(entry["id"])
    return connections


def format_history_context(history: list[dict], max_recent: int = 20) -> str | None:
    if not history:
        return None
    recent = history[-max_recent:]
    lines = []
    for entry in recent:
        tags = ", ".join(entry.get("tags", []))
        thread = entry.get("next_thread", "")
        lines.append(
            f'[{entry["id"]}] "{entry["title"]}" ({entry["timestamp"][:10]}) '
            f"-- Tags: {tags} -- Thread: {thread}"
        )
    return "\n".join(lines)


def get_recent_titles(history: list[dict], n: int = 10) -> list[str]:
    return [e["title"] for e in history[-n:]]


def get_all_threads(history: list[dict]) -> list[dict]:
    threads = []
    for entry in history:
        if entry.get("next_thread"):
            threads.append({
                "id": entry["id"],
                "title": entry["title"],
                "thread": entry["next_thread"],
                "date": entry["timestamp"][:10],
                "mode": entry["mode"],
            })
    return threads


def search_history(query: str, history: list[dict]) -> list[dict]:
    query_lower = query.lower()
    results = []
    for entry in history:
        score = 0
        title = entry.get("title", "").lower()
        narrative = entry.get("narrative", "").lower()
        tags = [t.lower() for t in entry.get("tags", [])]
        thread = entry.get("next_thread", "").lower()

        if query_lower in title:
            score += 3
        if any(query_lower in t for t in tags):
            score += 2
        if query_lower in thread:
            score += 1
        if query_lower in narrative:
            score += 1

        if score > 0:
            results.append((score, entry))

    results.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in results]
