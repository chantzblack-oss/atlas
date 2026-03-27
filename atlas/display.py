import io
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

# Force UTF-8 output to avoid Windows cp1252 encoding errors
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(force_terminal=True)

MODE_STYLES = {
    "surprise": ("green", "SURPRISE"),
    "thread": ("blue", "THREAD"),
    "deep": ("magenta", "DEEP"),
}


def display_exploration(exploration) -> None:
    console.print()

    # Mode badge + Title
    mode_color, mode_label = MODE_STYLES.get(exploration.mode, ("white", "?"))
    header = Text()
    header.append(f" {mode_label} ", style=f"bold white on {mode_color}")
    header.append("  ")
    header.append(exploration.title, style="bold white")

    console.print(Panel(
        header,
        border_style="bright_black",
        padding=(1, 3),
    ))
    console.print()

    # Narrative
    console.print(Markdown(exploration.narrative), width=min(console.width, 100))
    console.print()

    # Sources
    if exploration.sources:
        console.print("[bright_black]" + "─" * min(console.width, 80) + "[/bright_black]")
        console.print()
        source_count = min(len(exploration.sources), 12)
        for src in exploration.sources[:source_count]:
            title = src.get("title", src["url"])
            url = src["url"]
            console.print(f"  [bright_black]{url}[/bright_black]")
            console.print(f"  [dim]{title}[/dim]")
            console.print()

    # Next thread
    if exploration.next_thread:
        console.print(Panel(
            Text(exploration.next_thread, style="italic yellow"),
            title="[bold yellow]Next Thread[/bold yellow]",
            subtitle=f"[dim]atlas thread {exploration.next_thread[:40]}...[/dim]",
            border_style="yellow",
            padding=(1, 3),
        ))
        console.print()

    # Footer: tags + ID
    footer = Text()
    if exploration.tags:
        for tag in exploration.tags:
            footer.append(f"#{tag} ", style="dim")
    footer.append(f"  [{exploration.id}]", style="bright_black")
    console.print(footer)
    console.print()


def display_history(history: list[dict]) -> None:
    if not history:
        console.print(
            "\n[dim]No explorations yet. "
            "Run [bold]atlas surprise[/bold] to start.[/dim]\n"
        )
        return

    console.print()
    console.print(f"[bold cyan]  {len(history)} explorations[/bold cyan]")
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
    table.add_column("Mode", width=9)
    table.add_column("Title", style="bold")
    table.add_column("Date", style="dim", width=12)
    table.add_column("Tags", style="dim")

    for entry in reversed(history[-30:]):
        mode_color, mode_label = MODE_STYLES.get(entry["mode"], ("white", "?"))
        tags = " ".join(f"#{t}" for t in entry.get("tags", [])[:4])
        date = entry["timestamp"][:10]
        table.add_row(
            entry["id"],
            f"[{mode_color}]{mode_label}[/{mode_color}]",
            entry["title"],
            date,
            tags,
        )

    console.print(table)
    console.print()
    console.print("[dim]  Revisit: atlas revisit <id>[/dim]")
    console.print()


def display_threads(threads: list[dict]) -> None:
    if not threads:
        console.print(
            "\n[dim]No threads yet. Explore something first.[/dim]\n"
        )
        return

    console.print()
    console.print(f"[bold yellow]  {len(threads)} open threads[/bold yellow]")
    console.print()

    for t in reversed(threads[-20:]):
        mode_color, _ = MODE_STYLES.get(t["mode"], ("white", "?"))
        console.print(
            f"  [{mode_color}]{t['id']}[/{mode_color}] "
            f"[bold]{t['title']}[/bold] [dim]({t['date']})[/dim]"
        )
        console.print(f"         [italic yellow]{t['thread']}[/italic yellow]")
        console.print()

    console.print("[dim]  Pull a thread: atlas thread \"<paste thread here>\"[/dim]")
    console.print()


def display_search_results(query: str, results: list[dict]) -> None:
    if not results:
        console.print(f"\n[dim]No results for \"{query}\".[/dim]\n")
        return

    console.print()
    console.print(
        f"[bold cyan]  {len(results)} results for \"{query}\"[/bold cyan]"
    )
    console.print()

    for entry in results[:15]:
        mode_color, mode_label = MODE_STYLES.get(entry["mode"], ("white", "?"))
        tags = " ".join(f"#{t}" for t in entry.get("tags", [])[:4])
        date = entry["timestamp"][:10]
        console.print(
            f"  [{mode_color}]{entry['id']}[/{mode_color}] "
            f"[bold]{entry['title']}[/bold] [dim]({date})[/dim]"
        )
        if tags:
            console.print(f"         [dim]{tags}[/dim]")
        if entry.get("next_thread"):
            console.print(
                f"         [dim italic]{entry['next_thread'][:80]}[/dim italic]"
            )
        console.print()
