import io
import sys
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.rule import Rule
from rich.columns import Columns
from rich.tree import Tree

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(force_terminal=True)

MODE_STYLES = {
    "surprise": ("green", "SURPRISE"),
    "thread": ("blue", "THREAD"),
    "deep": ("magenta", "DEEP DIVE"),
    "podcast": ("red", "PODCAST"),
}

STYLE_LABELS = {
    "story": ("yellow", "STORY"),
    "myth-buster": ("cyan", "MYTH-BUSTER"),
    "scale": ("bright_magenta", "SCALE"),
}


def _estimate_read_time(text: str) -> int:
    words = len(text.split())
    return max(1, round(words / 230))


def _truncate(text: str, length: int) -> str:
    return text[:length] + "..." if len(text) > length else text


def _reveal_narrative(narrative: str, width: int) -> None:
    """Reveal narrative paragraph by paragraph with cinematic pacing."""
    paragraphs = [p.strip() for p in narrative.split("\n\n") if p.strip()]

    for i, para in enumerate(paragraphs):
        console.print(Markdown(para), width=width)

        if i < len(paragraphs) - 1:
            console.print()
            words = len(para.split())
            is_header = para.lstrip().startswith("#")
            is_quote = para.lstrip().startswith(">")

            # Pacing: let hooks and punchy lines land
            if is_header:
                time.sleep(0.18)
            elif is_quote or words < 15:
                time.sleep(0.14)
            elif words < 40:
                time.sleep(0.09)
            else:
                time.sleep(0.05)


def _display_sources(sources: list[dict], width: int) -> None:
    """Display sources as numbered clickable hyperlinks."""
    if not sources:
        return

    console.print()
    console.print(
        Rule(
            title="[bold bright_black]Follow Up[/bold bright_black]",
            style="bright_black",
        ),
        width=width,
    )
    console.print()

    for i, src in enumerate(sources[:12], 1):
        title = src.get("title", "")
        url = src["url"]

        if not title or title == url:
            # Derive a readable title from the URL
            parts = [p for p in url.rstrip("/").split("/") if p]
            title = parts[-1] if parts else url

        console.print(
            f"  [dim]{i:>2}.[/dim]  "
            f"[link={url}][bold]{_truncate(title, 60)}[/bold][/link]"
        )
        console.print(
            f"       [dim bright_black]{_truncate(url, 70)}[/dim bright_black]"
        )

    console.print()


def _display_connections(connections: list[str], history: list[dict]) -> None:
    """Show connected past explorations for the rabbit-hole effect."""
    if not connections or not history:
        return

    # Build ID -> entry lookup
    lookup = {e["id"]: e for e in history}
    found = [(cid, lookup[cid]) for cid in connections if cid in lookup]

    if not found:
        return

    console.print(
        "  [bold bright_black]Connected explorations[/bold bright_black]"
    )
    for cid, entry in found[:5]:
        mode_color, _ = MODE_STYLES.get(entry.get("mode", ""), ("white", "?"))
        console.print(
            f"  [{mode_color}]{cid}[/{mode_color}]  "
            f"[dim]{entry.get('title', 'Untitled')}[/dim]"
        )
    console.print()


def _display_threads(exploration, width: int) -> None:
    """Display next threads — supports both single and multi-thread."""
    threads = getattr(exploration, "next_threads", [])
    single = getattr(exploration, "next_thread", "")

    # Normalize: use multi-thread if available, else single
    if not threads and single:
        threads = [single]

    if not threads:
        return

    if len(threads) == 1:
        # Single thread — classic yellow panel
        console.print(Panel(
            Text(threads[0], style="italic yellow"),
            title="[bold yellow]Next Thread[/bold yellow]",
            subtitle="[dim]atlas next[/dim]",
            border_style="yellow",
            padding=(1, 3),
        ))
    else:
        # Multiple threads — numbered pick list
        thread_text = Text()
        for i, thread in enumerate(threads, 1):
            thread_text.append(f"  {i}. ", style="bold yellow")
            thread_text.append(thread, style="italic yellow")
            if i < len(threads):
                thread_text.append("\n\n")

        console.print(Panel(
            thread_text,
            title="[bold yellow]Choose Your Rabbit Hole[/bold yellow]",
            subtitle="[dim]pick 1, 2, or 3[/dim]",
            border_style="yellow",
            padding=(1, 3),
        ))

    console.print()


def display_exploration(exploration, history: list[dict] | None = None) -> None:
    width = min(console.width, 100)
    console.print()

    # -- Header --
    mode_color, mode_label = MODE_STYLES.get(
        exploration.mode, ("white", "?"))
    read_min = _estimate_read_time(exploration.narrative)

    header = Text()
    header.append(f" {mode_label} ", style=f"bold white on {mode_color}")

    # Style badge
    style = getattr(exploration, "style", None)
    if style and style in STYLE_LABELS:
        style_color, style_label = STYLE_LABELS[style]
        header.append("  ")
        header.append(f" {style_label} ",
                      style=f"bold white on {style_color}")

    header.append("  ")
    header.append(exploration.title, style="bold white")
    header.append(f"     ~{read_min} min", style="dim italic")

    console.print(Panel(
        header,
        border_style=mode_color,
        padding=(1, 3),
    ))
    console.print()
    time.sleep(0.4)  # beat before the hook lands

    # -- Narrative --
    _reveal_narrative(exploration.narrative, width)
    console.print()

    # -- Sources --
    _display_sources(exploration.sources, width)

    # -- Connected explorations --
    if history and exploration.connections:
        _display_connections(exploration.connections, history)

    # -- Next Threads --
    _display_threads(exploration, width)

    # -- Footer --
    footer = Text()
    if exploration.tags:
        for tag in exploration.tags:
            footer.append(f"#{tag} ", style="dim")
    footer.append(f"[{exploration.id}]", style="bright_black")
    console.print(footer)
    console.print()


def display_podcast_card(exploration, voice_name: str = "andrew") -> None:
    """Compact 'Now Playing' card for podcast mode."""
    words = len(exploration.narrative.split())
    est_min = max(1, round(words / 150))

    card = Text()
    card.append(" PODCAST ", style="bold white on red")
    card.append("  ")
    card.append(exploration.title, style="bold white")
    card.append("\n\n  ")
    card.append(f"~{est_min} min", style="dim")
    card.append("  \u00b7  ", style="dim")
    card.append(voice_name, style="dim")
    if exploration.tags:
        tags = " ".join(f"#{t}" for t in exploration.tags[:4])
        card.append("  \u00b7  ", style="dim")
        card.append(tags, style="dim")

    console.print()
    console.print(Panel(
        card,
        border_style="red",
        padding=(1, 3),
    ))


def display_podcast_transcript(exploration) -> None:
    """Show the podcast script as readable text."""
    width = min(console.width, 100)
    console.print()
    console.print("  [bold bright_black]--- Transcript ---[/bold bright_black]")
    console.print()
    for para in exploration.narrative.split("\n\n"):
        stripped = para.strip()
        if stripped:
            console.print(f"  {stripped}", width=width)
            console.print()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# History / browse views
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def display_history(history: list[dict]) -> None:
    if not history:
        console.print(
            "\n[dim]No explorations yet. Run [bold]atlas[/bold] to start.[/dim]\n"
        )
        return

    console.print()
    console.print(f"  [bold cyan]{len(history)} explorations[/bold cyan]")
    console.print()

    table = Table(
        show_header=True,
        header_style="bold bright_black",
        border_style="bright_black",
        show_lines=False,
        padding=(0, 1),
        expand=False,
    )
    table.add_column("ID", style="bright_black", width=10)
    table.add_column("Mode", width=11)
    table.add_column("Title", style="bold")
    table.add_column("Style", style="dim", width=13)
    table.add_column("Date", style="dim", width=12)
    table.add_column("Tags", style="dim")

    for entry in reversed(history[-30:]):
        mode_color, mode_label = MODE_STYLES.get(
            entry["mode"], ("white", "?"))
        tags = " ".join(f"#{t}" for t in entry.get("tags", [])[:3])
        date = entry["timestamp"][:10]
        style = entry.get("style", "") or ""
        table.add_row(
            entry["id"],
            f"[{mode_color}]{mode_label}[/{mode_color}]",
            entry["title"],
            style,
            date,
            tags,
        )

    console.print(table)
    console.print()
    console.print("[dim]  atlas revisit <id>   atlas next <id>[/dim]")
    console.print()


def display_threads(threads: list[dict]) -> None:
    if not threads:
        console.print(
            "\n[dim]No threads yet. Run [bold]atlas[/bold] to start.[/dim]\n"
        )
        return

    console.print()
    console.print(f"  [bold yellow]{len(threads)} open threads[/bold yellow]")
    console.print()

    for t in reversed(threads[-20:]):
        mode_color, _ = MODE_STYLES.get(t["mode"], ("white", "?"))
        console.print(
            f"  [{mode_color}]{t['id']}[/{mode_color}]  "
            f"[bold]{t['title']}[/bold]  [dim]{t['date']}[/dim]"
        )
        # Show all threads if available
        all_threads = t.get("threads", [t["thread"]])
        for i, thread in enumerate(all_threads, 1):
            prefix = f"  {i}." if len(all_threads) > 1 else "   "
            console.print(
                f"      {prefix} [italic yellow]{thread}[/italic yellow]"
            )
        console.print()

    console.print("[dim]  atlas next <id>[/dim]")
    console.print()


def display_search_results(query: str, results: list[dict]) -> None:
    if not results:
        console.print(f"\n[dim]No results for \"{query}\".[/dim]\n")
        return

    console.print()
    console.print(
        f"  [bold cyan]{len(results)} results for \"{query}\"[/bold cyan]"
    )
    console.print()

    for entry in results[:15]:
        mode_color, _ = MODE_STYLES.get(entry["mode"], ("white", "?"))
        date = entry["timestamp"][:10]
        style = entry.get("style", "")
        style_str = f" [{style}]" if style else ""
        console.print(
            f"  [{mode_color}]{entry['id']}[/{mode_color}]  "
            f"[bold]{entry['title']}[/bold]{style_str}  [dim]{date}[/dim]"
        )
        tags = " ".join(f"#{t}" for t in entry.get("tags", [])[:3])
        if tags:
            console.print(f"       [dim]{tags}[/dim]")
        threads = entry.get("next_threads", [])
        if not threads:
            single = entry.get("next_thread", "")
            if single:
                threads = [single]
        if threads:
            console.print(
                f"       [dim italic]"
                f"{_truncate(threads[0], 75)}"
                f"[/dim italic]"
            )
        console.print()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Journey map
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def display_journey(history: list[dict]) -> None:
    """Show the explorer's thematic journey as a visual map."""
    from .history import analyze_themes, find_thematic_clusters

    if not history:
        console.print(
            "\n  [dim]No explorations yet. Start exploring to build "
            "your journey map.[/dim]\n"
        )
        return

    console.print()
    console.print(
        f"  [bold cyan]Your Exploration Journey[/bold cyan]  "
        f"[dim]{len(history)} explorations[/dim]"
    )
    console.print()

    # Theme clusters
    clusters = find_thematic_clusters(history)
    if clusters:
        for cluster in clusters[:8]:
            theme = cluster["theme"]
            count = cluster["count"]

            tree = Tree(
                f"  [bold]{theme}[/bold]  "
                f"[dim]{count} explorations[/dim]"
            )

            entries = cluster["entries"]
            for entry in entries[-5:]:  # show last 5 per cluster
                mode_color, _ = MODE_STYLES.get(
                    entry["mode"], ("white", "?"))
                style = entry.get("style", "")
                style_str = f" [{style}]" if style else ""
                tree.add(
                    f"[{mode_color}]{entry['id']}[/{mode_color}]  "
                    f"[dim]{entry['title']}{style_str}[/dim]"
                )

            if len(entries) > 5:
                tree.add(f"[dim]... and {len(entries) - 5} more[/dim]")

            console.print(tree)
            console.print()
    else:
        console.print(
            "  [dim]Keep exploring — clusters will appear as themes "
            "emerge.[/dim]"
        )
        console.print()

    # Top tags
    themes = analyze_themes(history)
    if themes:
        top = list(themes.items())[:12]
        tag_line = Text("  ")
        for tag, count in top:
            tag_line.append(f"#{tag}", style="bold")
            tag_line.append(f"({count}) ", style="dim")
        console.print(tag_line)
        console.print()

    # Exploration styles used
    from collections import Counter
    style_counts = Counter(
        e.get("style") for e in history if e.get("style"))
    if style_counts:
        style_line = Text("  Styles: ")
        for style, count in style_counts.most_common():
            color, label = STYLE_LABELS.get(style, ("white", style))
            style_line.append(f"{label}", style=f"bold {color}")
            style_line.append(f"({count}) ", style="dim")
        console.print(style_line)
        console.print()

    # Uncovered connections
    if len(clusters) >= 2:
        console.print(
            "  [dim italic]Tip: Look for connections between your clusters — "
            "the best discoveries happen at the intersections.[/dim italic]"
        )
        console.print()
