import json
from collections import Counter
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


def _get_threads(entry: dict) -> list[str]:
    """Get all threads from an entry, handling both old and new format."""
    threads = entry.get("next_threads", [])
    if not threads:
        single = entry.get("next_thread", "")
        if single:
            threads = [single]
    return threads


def format_history_context(history: list[dict], max_recent: int = 20) -> str | None:
    if not history:
        return None
    recent = history[-max_recent:]
    lines = []
    for entry in recent:
        tags = ", ".join(entry.get("tags", []))
        threads = _get_threads(entry)
        thread_str = threads[0] if threads else ""
        style = entry.get("style", "")
        style_str = f" [{style}]" if style else ""
        lines.append(
            f'[{entry["id"]}] "{entry["title"]}"{style_str} '
            f'({entry["timestamp"][:10]}) '
            f"-- Tags: {tags} -- Thread: {thread_str}"
        )
    return "\n".join(lines)


def get_recent_titles(history: list[dict], n: int = 10) -> list[str]:
    return [e["title"] for e in history[-n:]]


def get_all_threads(history: list[dict]) -> list[dict]:
    threads = []
    for entry in history:
        entry_threads = _get_threads(entry)
        if entry_threads:
            threads.append({
                "id": entry["id"],
                "title": entry["title"],
                "thread": entry_threads[0],
                "threads": entry_threads,
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
        threads = _get_threads(entry)
        threads_text = " ".join(t.lower() for t in threads)

        if query_lower in title:
            score += 3
        if any(query_lower in t for t in tags):
            score += 2
        if query_lower in threads_text:
            score += 1
        if query_lower in narrative:
            score += 1

        if score > 0:
            results.append((score, entry))

    results.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in results]


# -- Journey awareness ---------------------------------------------------


def analyze_themes(history: list[dict]) -> dict[str, int]:
    """Count tag frequency across all history entries."""
    counts = Counter()
    for entry in history:
        for tag in entry.get("tags", []):
            counts[tag.lower()] += 1
    return dict(counts.most_common())


def find_thematic_clusters(history: list[dict],
                           min_cluster: int = 2) -> list[dict]:
    """Group explorations by overlapping tags into thematic clusters."""
    if not history:
        return []

    # Build tag -> exploration mapping
    tag_to_ids = {}
    id_to_entry = {}
    for entry in history:
        eid = entry["id"]
        id_to_entry[eid] = entry
        for tag in entry.get("tags", []):
            tag_lower = tag.lower()
            tag_to_ids.setdefault(tag_lower, set()).add(eid)

    # Find clusters: groups of tags that frequently co-occur
    theme_counts = analyze_themes(history)
    top_tags = [t for t, c in theme_counts.items() if c >= min_cluster]

    clusters = []
    seen_ids = set()

    for tag in top_tags:
        ids = tag_to_ids.get(tag, set())
        # Only create cluster if it has explorations we haven't fully covered
        new_ids = ids - seen_ids
        if len(ids) >= min_cluster and new_ids:
            # Find related tags (co-occurring)
            related_tags = Counter()
            for eid in ids:
                for t in id_to_entry[eid].get("tags", []):
                    t_lower = t.lower()
                    if t_lower != tag:
                        related_tags[t_lower] += 1

            top_related = [t for t, c in related_tags.most_common(3) if c >= 2]
            theme_name = " & ".join([tag] + top_related[:1]) if top_related else tag

            clusters.append({
                "theme": theme_name,
                "primary_tag": tag,
                "count": len(ids),
                "exploration_ids": sorted(ids),
                "related_tags": top_related,
                "entries": [id_to_entry[eid] for eid in sorted(ids)],
            })
            seen_ids |= ids

    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def format_journey_context(history: list[dict]) -> str | None:
    """Build a narrative context string summarizing thematic patterns."""
    if len(history) < 3:
        return None

    themes = analyze_themes(history)
    if not themes:
        return None

    # Top themes
    top = list(themes.items())[:6]
    if not top:
        return None

    lines = []

    # Theme summary
    theme_parts = [f"{tag} ({count})" for tag, count in top]
    lines.append(f"Top themes across {len(history)} explorations: "
                 + ", ".join(theme_parts))

    # Recent trajectory (last 5)
    recent = history[-5:]
    recent_tags = Counter()
    for entry in recent:
        for tag in entry.get("tags", []):
            recent_tags[tag.lower()] += 1
    if recent_tags:
        trending = [t for t, c in recent_tags.most_common(3)]
        lines.append(f"Recent focus: {', '.join(trending)}")

    # Clusters
    clusters = find_thematic_clusters(history)
    if clusters:
        for cluster in clusters[:3]:
            lines.append(
                f"Cluster: \"{cluster['theme']}\" — "
                f"{cluster['count']} explorations"
            )

    # Styles used
    style_counts = Counter()
    for entry in history:
        s = entry.get("style")
        if s:
            style_counts[s] += 1
    if style_counts:
        fav = style_counts.most_common(1)[0]
        lines.append(f"Preferred style: {fav[0]} (used {fav[1]} times)")

    return "\n".join(lines)
