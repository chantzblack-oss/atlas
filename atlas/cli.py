import sys

import click
from rich.prompt import Prompt

from .engine import explore, MODEL
from . import config
from .history import load_history, get_all_threads, search_history
from .audio import VOICES
from .display import (
    console, display_exploration, display_history,
    display_threads, display_search_results,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Interactive menu
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _interactive_menu(ctx):
    """Show the ATLAS main menu."""
    history = load_history()
    last_thread = ""
    last_title = ""
    if history:
        last_thread = history[-1].get("next_thread", "")
        last_title = history[-1].get("title", "")

    cfg = config.load()
    badges = []
    if cfg.get("listen"):
        badges.append("podcast on")
    badges.append(cfg.get("model", "opus"))
    badge_str = " | ".join(badges)

    console.print()
    console.print(
        "  [bold cyan]A T L A S[/bold cyan]  "
        f"[dim]{badge_str}[/dim]"
    )
    console.print("  [dim]pocket Veritasium / Kurzgesagt[/dim]")
    console.print()
    console.print("  [bold white]1[/bold white]  Surprise me")
    console.print("  [bold white]2[/bold white]  Pull a thread")
    console.print("  [bold white]3[/bold white]  Deep dive")
    if last_thread:
        thread_preview = last_thread[:55] + "..." if len(last_thread) > 55 else last_thread
        console.print("  [bold white]4[/bold white]  Follow last thread")
        console.print(f"     [dim italic yellow]{thread_preview}[/dim italic yellow]")
    if last_title:
        console.print(f"  [bold white]5[/bold white]  Listen  [dim]({last_title})[/dim]")
    else:
        console.print("  [bold white]5[/bold white]  Listen")
    console.print("  [bold white]6[/bold white]  History")
    console.print("  [bold white]7[/bold white]  Settings")
    console.print()

    valid = ["1", "2", "3", "5", "6", "7"]
    if last_thread:
        valid.append("4")

    choice = Prompt.ask(
        "  [dim]>[/dim]", choices=sorted(valid),
        default="1", show_choices=False,
    )

    if choice == "1":
        ctx.invoke(surprise_cmd)

    elif choice == "2":
        idea = Prompt.ask("\n  [dim]What are you curious about?[/dim]")
        if idea.strip():
            result, hist = explore(
                mode="thread", user_input=idea.strip(),
                model=ctx.obj["model"])
            display_exploration(result, history=hist)
            _maybe_listen(ctx, result)

    elif choice == "3":
        topic = Prompt.ask("\n  [dim]Topic?[/dim]")
        if topic.strip():
            angle = Prompt.ask("  [dim]Angle (optional, enter to skip)[/dim]", default="")
            result, hist = explore(
                mode="deep", user_input=topic.strip(),
                angle=angle.strip() or None, model=ctx.obj["model"])
            display_exploration(result, history=hist)
            _maybe_listen(ctx, result)

    elif choice == "4":
        ctx.invoke(next_cmd, exploration_id=None)

    elif choice == "5":
        ctx.invoke(listen_cmd, exploration_id=None, voice=ctx.obj["voice"])

    elif choice == "6":
        ctx.invoke(history_cmd)

    elif choice == "7":
        _settings_menu()


def _settings_menu():
    """Interactive settings editor."""
    cfg = config.load()

    while True:
        console.print()
        console.print("  [bold cyan]Settings[/bold cyan]")
        console.print()
        console.print(
            f"  [bold white]1[/bold white]  Model    "
            f"[bold]{cfg.get('model', 'opus')}[/bold]"
        )
        console.print(
            f"  [bold white]2[/bold white]  Voice    "
            f"[bold]{cfg.get('voice', 'andrew')}[/bold]"
        )
        listen_status = "[bold green]on[/bold green]" if cfg.get("listen") else "[dim]off[/dim]"
        console.print(
            f"  [bold white]3[/bold white]  Podcast  "
            f"{listen_status}"
        )
        console.print(f"  [bold white]4[/bold white]  Back")
        console.print()

        choice = Prompt.ask(
            "  [dim]>[/dim]", choices=["1", "2", "3", "4"],
            default="4", show_choices=False,
        )

        if choice == "1":
            val = Prompt.ask(
                "  [dim]Model[/dim]",
                choices=["opus", "sonnet", "haiku"],
                default=cfg.get("model", "opus"),
            )
            config.set_value("model", val)
            cfg["model"] = val
            console.print("  [dim green]Saved.[/dim green]")

        elif choice == "2":
            val = Prompt.ask(
                "  [dim]Voice[/dim]",
                choices=sorted(VOICES.keys()),
                default=cfg.get("voice", "andrew"),
            )
            config.set_value("voice", val)
            cfg["voice"] = val
            console.print("  [dim green]Saved.[/dim green]")

        elif choice == "3":
            currently_on = cfg.get("listen", False)
            new_val = not currently_on
            config.set_value("listen", new_val)
            cfg["listen"] = new_val
            state = "[green]on[/green]" if new_val else "[dim]off[/dim]"
            console.print(f"  Podcast mode: {state}")

        elif choice == "4":
            break


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI group + commands
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@click.group(invoke_without_command=True)
@click.option("--model", "-m", default=None,
              help="Model: opus, sonnet, haiku")
@click.option("--listen", "-l", is_flag=True, default=False,
              help="Play as podcast after exploring")
@click.option("--voice", "-v", default=None,
              help="TTS voice: andrew, brian, ava, emma, aria, chris")
@click.pass_context
def cli(ctx, model, listen, voice):
    """ATLAS \u2014 pocket Veritasium / Kurzgesagt."""
    ctx.ensure_object(dict)

    # Merge: CLI flags override persistent config
    cfg = config.load()
    ctx.obj["model"] = model or cfg.get("model", MODEL)
    ctx.obj["listen"] = listen or cfg.get("listen", False)
    ctx.obj["voice"] = voice or cfg.get("voice", "andrew")

    if ctx.invoked_subcommand is None:
        _interactive_menu(ctx)


def _maybe_listen(ctx, exploration):
    if ctx.obj.get("listen"):
        from .audio import play_solo
        play_solo(exploration, voice=ctx.obj.get("voice", "andrew"))


@cli.command("surprise")
@click.pass_context
def surprise_cmd(ctx):
    """Let ATLAS pick something mind-blowing."""
    result, history = explore(mode="surprise", model=ctx.obj["model"])
    display_exploration(result, history=history)
    _maybe_listen(ctx, result)


@cli.command("thread")
@click.argument("idea", nargs=-1, required=True)
@click.pass_context
def thread_cmd(ctx, idea):
    """Pull a thread from a vague idea.

    Example: atlas thread "why do hospitals smell like that"
    """
    result, history = explore(
        mode="thread", user_input=" ".join(idea),
        model=ctx.obj["model"])
    display_exploration(result, history=history)
    _maybe_listen(ctx, result)


@cli.command("deep")
@click.argument("topic", nargs=-1, required=True)
@click.option("--angle", "-a", help="A specific angle or question")
@click.pass_context
def deep_cmd(ctx, topic, angle):
    """Kurzgesagt-style deep dive with real sources.

    Example: atlas deep "CRISPR" --angle "off-target effects"
    """
    result, history = explore(
        mode="deep", user_input=" ".join(topic),
        angle=angle, model=ctx.obj["model"])
    display_exploration(result, history=history)
    _maybe_listen(ctx, result)


@cli.command("next")
@click.argument("exploration_id", required=False)
@click.pass_context
def next_cmd(ctx, exploration_id):
    """Follow the thread from your last (or specified) exploration."""
    history = load_history()
    if not history:
        click.echo("No explorations yet. Run 'atlas' to start.")
        return

    if exploration_id:
        entry = next((e for e in history if e["id"] == exploration_id), None)
        if not entry:
            click.echo(f"'{exploration_id}' not found. Run 'atlas history'.")
            return
    else:
        entry = history[-1]

    thread = entry.get("next_thread", "")
    if not thread:
        click.echo(f"'{entry['id']}' has no next thread.")
        return

    result, hist = explore(
        mode="thread", user_input=thread, model=ctx.obj["model"])
    display_exploration(result, history=hist)
    _maybe_listen(ctx, result)


@cli.command("listen")
@click.argument("exploration_id", required=False)
@click.option("--voice", "-v", default=None,
              help="TTS voice: andrew, brian, ava, emma, aria, chris")
@click.pass_context
def listen_cmd(ctx, exploration_id, voice):
    """Listen to an exploration.

    \b
    Examples:
      atlas listen              play the most recent
      atlas listen a43aef79     play a specific one
      atlas listen voices       list available voices
    """
    from .audio import play_solo, list_voices

    voice = voice or ctx.obj.get("voice", "andrew")

    if exploration_id == "voices":
        list_voices()
        return

    history = load_history()
    if not history:
        click.echo("No explorations yet. Run 'atlas' to start.")
        return

    if exploration_id:
        entry = next((e for e in history if e["id"] == exploration_id), None)
        if not entry:
            click.echo(f"'{exploration_id}' not found.")
            return
    else:
        entry = history[-1]

    from .engine import Exploration
    exp = Exploration(
        **{k: entry[k] for k in Exploration.__dataclass_fields__})
    play_solo(exp, voice=voice)


@cli.command("config")
@click.argument("key", required=False)
@click.argument("value", required=False)
def config_cmd(key, value):
    """View or change settings.

    \b
    Examples:
      atlas config                show all settings
      atlas config model sonnet   set default model
      atlas config voice emma     set default voice
      atlas config listen on      always play as podcast
      atlas config listen off     text only
    """
    if key and value:
        if key == "listen":
            value = value.lower() in ("on", "true", "yes", "1")
        config.set_value(key, value)
        console.print(f"  [dim green]{key} = {value}[/dim green]")
        return

    cfg = config.load()
    console.print()
    console.print("  [bold cyan]Settings[/bold cyan]  [dim](atlas config <key> <value>)[/dim]")
    console.print()
    for k, v in cfg.items():
        if k == "listen":
            display = "[green]on[/green]" if v else "[dim]off[/dim]"
        else:
            display = f"[bold]{v}[/bold]"
        console.print(f"  {k:10s} {display}")
    console.print()


@cli.command("history")
def history_cmd():
    """Browse past explorations."""
    display_history(load_history())


@cli.command("threads")
def threads_cmd():
    """Show all open threads from past explorations."""
    display_threads(get_all_threads(load_history()))


@cli.command("search")
@click.argument("query", nargs=-1, required=True)
def search_cmd(query):
    """Search past explorations by keyword."""
    query_text = " ".join(query)
    results = search_history(query_text, load_history())
    display_search_results(query_text, results)


@cli.command("revisit")
@click.argument("exploration_id")
def revisit_cmd(exploration_id):
    """Re-read a past exploration by ID."""
    history = load_history()
    for entry in history:
        if entry["id"] == exploration_id:
            from .engine import Exploration
            exp = Exploration(
                **{k: entry[k] for k in Exploration.__dataclass_fields__})
            display_exploration(exp, history=history)
            return
    click.echo(f"'{exploration_id}' not found. Run 'atlas history'.")


if __name__ == "__main__":
    cli()
