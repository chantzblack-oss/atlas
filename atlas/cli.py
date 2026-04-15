import sys

import click
from rich.prompt import Prompt

from .engine import explore, Exploration, MODEL, STYLES
from . import config
from .history import load_history, get_all_threads, search_history
from .audio import VOICES
from .exceptions import AtlasError, SubprocessError
from .display import (
    console, display_exploration, display_history,
    display_threads, display_search_results, display_journey,
    display_podcast_card, display_podcast_transcript, MODE_STYLES,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _show_error(e):
    """Display an AtlasError to the user."""
    if isinstance(e, SubprocessError) and e.stderr:
        console.print(f"\n  [bold red]{e}[/bold red]")
        console.print(f"  [dim]{e.stderr[:300]}[/dim]")
    else:
        console.print(f"\n  [bold red]{e}[/bold red]")


def _pick_style() -> str | None:
    """Prompt user for storytelling style. Returns None for default."""
    console.print()
    console.print(
        "  [dim]Style?[/dim]  "
        "[bold white]enter[/bold white][dim]=default[/dim]  "
        "[bold white]s[/bold white][dim]=story[/dim]  "
        "[bold white]m[/bold white][dim]=myth-buster[/dim]  "
        "[bold white]k[/bold white][dim]=scale[/dim]"
    )
    raw = Prompt.ask("  [dim]>[/dim]", default="")
    return {"s": "story", "m": "myth-buster", "k": "scale"}.get(
        raw.strip().lower())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Post-exploration menu
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _post_explore(ctx, result):
    """Post-exploration options. Returns True for menu, False to quit."""
    while True:
        console.print()
        threads = result.next_threads or (
            [result.next_thread] if result.next_thread else [])

        if len(threads) >= 3:
            for i, thread in enumerate(threads[:3], 1):
                preview = thread[:60] + "..." if len(thread) > 60 else thread
                console.print(
                    f"  [bold white]{i}[/bold white]  "
                    f"[italic yellow]{preview}[/italic yellow]"
                )
        elif len(threads) == 1:
            preview = threads[0][:60]
            if len(threads[0]) > 60:
                preview += "..."
            console.print(f"  [bold white]n[/bold white]  Follow the thread")
            console.print(
                f"     [dim italic yellow]{preview}[/dim italic yellow]")

        console.print("  [bold white]l[/bold white]  Listen")
        console.print("  [bold white]m[/bold white]  Menu")
        console.print("  [bold white]q[/bold white]  Quit")
        console.print()

        valid = ["l", "m", "q"]
        if len(threads) >= 3:
            valid.extend(["1", "2", "3"])
        elif threads:
            valid.append("n")

        choice = Prompt.ask(
            "  [dim]>[/dim]", choices=sorted(valid),
            default="m", show_choices=False,
        )

        thread_to_follow = None
        if choice in ("1", "2", "3"):
            idx = int(choice) - 1
            if idx < len(threads):
                thread_to_follow = threads[idx]
        elif choice == "n" and threads:
            thread_to_follow = threads[0]

        if thread_to_follow:
            style = _pick_style()
            try:
                new_result, hist = explore(
                    mode="thread", user_input=thread_to_follow,
                    style=style, model=ctx.obj["model"])
            except AtlasError as e:
                _show_error(e)
                return True
            display_exploration(new_result, history=hist)
            _maybe_listen(ctx, new_result)
            result = new_result
        elif choice == "l":
            from .audio import play_solo
            play_solo(result, voice=ctx.obj.get("voice", "andrew"))
        elif choice == "m":
            return True
        elif choice == "q":
            return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Post-podcast menu
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _post_podcast(ctx, result):
    """Post-podcast options. Returns True for menu, False to quit."""
    while True:
        threads = result.next_threads or (
            [result.next_thread] if result.next_thread else [])

        console.print()
        if threads:
            for i, thread in enumerate(threads[:3], 1):
                preview = thread[:60] + "..." if len(thread) > 60 else thread
                console.print(
                    f"  [bold white]{i}[/bold white]  "
                    f"[italic yellow]{preview}[/italic yellow]"
                )
        console.print("  [bold white]r[/bold white]  Replay")
        console.print("  [bold white]t[/bold white]  Transcript")
        console.print("  [bold white]n[/bold white]  New episode")
        console.print("  [bold white]m[/bold white]  Menu")
        console.print("  [bold white]q[/bold white]  Quit")
        console.print()

        valid = ["m", "n", "q", "r", "t"]
        if threads:
            valid.extend([str(i) for i in range(
                1, min(len(threads), 3) + 1)])

        choice = Prompt.ask(
            "  [dim]>[/dim]", choices=sorted(valid),
            default="m", show_choices=False,
        )

        if choice == "r":
            from .audio import play_solo
            play_solo(result, voice=ctx.obj.get("voice", "andrew"))
        elif choice == "t":
            display_podcast_transcript(result)
        elif choice in ("1", "2", "3"):
            idx = int(choice) - 1
            if idx < len(threads):
                style = _pick_style()
                try:
                    new_result, hist = explore(
                        mode="podcast", user_input=threads[idx],
                        style=style, model=ctx.obj["model"])
                except AtlasError as e:
                    _show_error(e)
                    return True
                display_podcast_card(
                    new_result,
                    voice_name=ctx.obj.get("voice", "andrew"))
                from .audio import play_solo
                play_solo(
                    new_result,
                    voice=ctx.obj.get("voice", "andrew"))
                result = new_result
        elif choice == "n":
            topic = Prompt.ask(
                "\n  [dim]What's the episode about? "
                "(enter for surprise)[/dim]",
                default="")
            style = _pick_style()
            try:
                new_result, hist = explore(
                    mode="podcast",
                    user_input=topic.strip() or None,
                    style=style, model=ctx.obj["model"])
            except AtlasError as e:
                _show_error(e)
                return True
            display_podcast_card(
                new_result,
                voice_name=ctx.obj.get("voice", "andrew"))
            from .audio import play_solo
            play_solo(
                new_result,
                voice=ctx.obj.get("voice", "andrew"))
            result = new_result
        elif choice == "m":
            return True
        elif choice == "q":
            return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Interactive menu
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _interactive_menu(ctx):
    """Main interactive loop."""
    while True:
        history = load_history()
        last_threads = []
        last_title = ""
        if history:
            last_entry = history[-1]
            last_threads = last_entry.get("next_threads", [])
            if not last_threads:
                single = last_entry.get("next_thread", "")
                if single:
                    last_threads = [single]
            last_title = last_entry.get("title", "")

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
        if last_threads:
            console.print(
                "  [bold white]4[/bold white]  Follow last thread")
            preview = last_threads[0][:55]
            if len(last_threads[0]) > 55:
                preview += "..."
            console.print(
                f"     [dim italic yellow]{preview}[/dim italic yellow]")
            if len(last_threads) > 1:
                console.print(
                    f"     [dim](+{len(last_threads) - 1} more)[/dim]")
        console.print("  [bold white]5[/bold white]  Podcast")
        if last_title:
            console.print(
                f"  [bold white]6[/bold white]  Listen  "
                f"[dim]({last_title})[/dim]")
        else:
            console.print("  [bold white]6[/bold white]  Listen")
        console.print("  [bold white]7[/bold white]  History")
        console.print("  [bold white]8[/bold white]  Journey map")
        console.print("  [bold white]9[/bold white]  Settings")
        console.print("  [bold white]q[/bold white]  Quit")
        console.print()

        valid = ["1", "2", "3", "5", "6", "7", "8", "9", "q"]
        if last_threads:
            valid.append("4")

        choice = Prompt.ask(
            "  [dim]>[/dim]", choices=sorted(valid),
            default="1", show_choices=False,
        )

        if choice == "q":
            break

        result = None

        if choice == "1":
            style = _pick_style()
            try:
                result, hist = explore(
                    mode="surprise", style=style,
                    model=ctx.obj["model"])
            except AtlasError as e:
                _show_error(e)
                continue
            display_exploration(result, history=hist)
            _maybe_listen(ctx, result)

        elif choice == "2":
            idea = Prompt.ask(
                "\n  [dim]What are you curious about?[/dim]")
            if not idea.strip():
                continue
            style = _pick_style()
            try:
                result, hist = explore(
                    mode="thread", user_input=idea.strip(),
                    style=style, model=ctx.obj["model"])
            except AtlasError as e:
                _show_error(e)
                continue
            display_exploration(result, history=hist)
            _maybe_listen(ctx, result)

        elif choice == "3":
            topic = Prompt.ask("\n  [dim]Topic?[/dim]")
            if not topic.strip():
                continue
            angle = Prompt.ask(
                "  [dim]Angle (optional, enter to skip)[/dim]",
                default="")
            style = _pick_style()
            try:
                result, hist = explore(
                    mode="deep", user_input=topic.strip(),
                    angle=angle.strip() or None, style=style,
                    model=ctx.obj["model"])
            except AtlasError as e:
                _show_error(e)
                continue
            display_exploration(result, history=hist)
            _maybe_listen(ctx, result)

        elif choice == "4":
            # Follow last thread — pick from multiple if available
            thread_to_follow = last_threads[0]
            if len(last_threads) > 1:
                console.print()
                for i, t in enumerate(last_threads[:3], 1):
                    preview = t[:65] + "..." if len(t) > 65 else t
                    console.print(
                        f"  [bold white]{i}[/bold white]  "
                        f"[italic yellow]{preview}[/italic yellow]")
                console.print()
                pick = Prompt.ask(
                    "  [dim]Which thread?[/dim]",
                    choices=[str(i) for i in range(
                        1, min(len(last_threads), 3) + 1)],
                    default="1",
                    show_choices=False,
                )
                thread_to_follow = last_threads[int(pick) - 1]
            style = _pick_style()
            try:
                result, hist = explore(
                    mode="thread", user_input=thread_to_follow,
                    style=style, model=ctx.obj["model"])
            except AtlasError as e:
                _show_error(e)
                continue
            display_exploration(result, history=hist)
            _maybe_listen(ctx, result)

        elif choice == "5":
            topic = Prompt.ask(
                "\n  [dim]What's the episode about? "
                "(enter for surprise)[/dim]",
                default="")
            style = _pick_style()
            try:
                result, hist = explore(
                    mode="podcast",
                    user_input=topic.strip() or None,
                    style=style, model=ctx.obj["model"])
            except AtlasError as e:
                _show_error(e)
                continue
            display_podcast_card(
                result, voice_name=ctx.obj.get("voice", "andrew"))
            from .audio import play_solo
            play_solo(result, voice=ctx.obj.get("voice", "andrew"))
            if not _post_podcast(ctx, result):
                break
            continue

        elif choice == "6":
            _listen_interactive(ctx)
            continue

        elif choice == "7":
            display_history(load_history())
            continue

        elif choice == "8":
            display_journey(load_history())
            continue

        elif choice == "9":
            _settings_menu()
            continue

        # After any exploration, show post-explore options
        if result is not None:
            if not _post_explore(ctx, result):
                break


def _listen_interactive(ctx):
    """Browse and play past explorations."""
    history = load_history()
    if not history:
        console.print("\n  [dim]No explorations yet.[/dim]")
        return

    recent = list(reversed(history[-10:]))
    console.print()
    console.print("  [bold cyan]Recent Episodes[/bold cyan]")
    console.print()

    for i, entry in enumerate(recent, 1):
        mode_color, _ = MODE_STYLES.get(
            entry.get("mode", ""), ("white", "?"))
        title = entry.get("title", "Untitled")
        date = entry.get("timestamp", "")[:10]
        console.print(
            f"  [bold white]{i:>2}[/bold white]  "
            f"[{mode_color}]{title}[/{mode_color}]  "
            f"[dim]{date}[/dim]"
        )

    console.print(f"\n  [bold white] b[/bold white]  Back\n")

    valid = [str(i) for i in range(1, len(recent) + 1)] + ["b"]
    choice = Prompt.ask(
        "  [dim]>[/dim]", choices=valid,
        default="1", show_choices=False,
    )

    if choice == "b":
        return

    idx = int(choice) - 1
    entry = recent[idx]
    exp = Exploration.from_dict(entry)
    from .audio import play_solo
    play_solo(exp, voice=ctx.obj.get("voice", "andrew"))


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
        listen_status = (
            "[bold green]on[/bold green]"
            if cfg.get("listen") else "[dim]off[/dim]")
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

STYLE_OPTION = click.option(
    "--style", "-s",
    type=click.Choice(STYLES),
    default=None,
    help="Storytelling style: story, myth-buster, scale",
)


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
@STYLE_OPTION
@click.pass_context
def surprise_cmd(ctx, style):
    """Let ATLAS pick something mind-blowing."""
    try:
        result, history = explore(
            mode="surprise", style=style, model=ctx.obj["model"])
    except AtlasError as e:
        _show_error(e)
        return
    display_exploration(result, history=history)
    _maybe_listen(ctx, result)


@cli.command("thread")
@click.argument("idea", nargs=-1, required=True)
@STYLE_OPTION
@click.pass_context
def thread_cmd(ctx, idea, style):
    """Pull a thread from a vague idea.

    Example: atlas thread "why do hospitals smell like that"
    """
    try:
        result, history = explore(
            mode="thread", user_input=" ".join(idea),
            style=style, model=ctx.obj["model"])
    except AtlasError as e:
        _show_error(e)
        return
    display_exploration(result, history=history)
    _maybe_listen(ctx, result)


@cli.command("deep")
@click.argument("topic", nargs=-1, required=True)
@click.option("--angle", "-a", help="A specific angle or question")
@STYLE_OPTION
@click.pass_context
def deep_cmd(ctx, topic, angle, style):
    """Kurzgesagt-style deep dive with real sources.

    Example: atlas deep "CRISPR" --angle "off-target effects"
    """
    try:
        result, history = explore(
            mode="deep", user_input=" ".join(topic),
            angle=angle, style=style, model=ctx.obj["model"])
    except AtlasError as e:
        _show_error(e)
        return
    display_exploration(result, history=history)
    _maybe_listen(ctx, result)


@cli.command("next")
@click.argument("exploration_id", required=False)
@STYLE_OPTION
@click.pass_context
def next_cmd(ctx, exploration_id, style):
    """Follow the thread from your last (or specified) exploration."""
    history = load_history()
    if not history:
        click.echo("No explorations yet. Run 'atlas' to start.")
        return

    if exploration_id:
        entry = next(
            (e for e in history if e["id"] == exploration_id), None)
        if not entry:
            click.echo(f"'{exploration_id}' not found. Run 'atlas history'.")
            return
    else:
        entry = history[-1]

    # Get threads — prefer multi-thread, fall back to single
    threads = entry.get("next_threads", [])
    if not threads:
        single = entry.get("next_thread", "")
        if single:
            threads = [single]

    if not threads:
        click.echo(f"'{entry['id']}' has no next thread.")
        return

    # If multiple threads, let user pick
    thread = threads[0]
    if len(threads) > 1:
        console.print()
        for i, t in enumerate(threads[:3], 1):
            preview = t[:65] + "..." if len(t) > 65 else t
            console.print(
                f"  [bold white]{i}[/bold white]  "
                f"[italic yellow]{preview}[/italic yellow]")
        console.print()
        pick = Prompt.ask(
            "  [dim]Which thread?[/dim]",
            choices=[str(i) for i in range(1, min(len(threads), 3) + 1)],
            default="1",
            show_choices=False,
        )
        thread = threads[int(pick) - 1]

    try:
        result, hist = explore(
            mode="thread", user_input=thread,
            style=style, model=ctx.obj["model"])
    except AtlasError as e:
        _show_error(e)
        return
    display_exploration(result, history=hist)
    _maybe_listen(ctx, result)


@cli.command("podcast")
@click.argument("topic", nargs=-1, required=False)
@STYLE_OPTION
@click.pass_context
def podcast_cmd(ctx, topic, style):
    """Generate and play a podcast episode.

    \b
    Examples:
      atlas podcast                              surprise episode
      atlas podcast "why do we dream"             episode on a topic
      atlas podcast "D-Day" --style story         cinematic narration
      atlas podcast "dark matter" --style scale   existential awe
    """
    user_input = " ".join(topic) if topic else None
    try:
        result, history = explore(
            mode="podcast", user_input=user_input,
            style=style, model=ctx.obj["model"])
    except AtlasError as e:
        _show_error(e)
        return
    display_podcast_card(result, voice_name=ctx.obj.get("voice", "andrew"))
    from .audio import play_solo
    play_solo(result, voice=ctx.obj.get("voice", "andrew"))


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
        entry = next(
            (e for e in history if e["id"] == exploration_id), None)
        if not entry:
            click.echo(f"'{exploration_id}' not found.")
            return
    else:
        entry = history[-1]

    exp = Exploration.from_dict(entry)
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
    console.print(
        "  [bold cyan]Settings[/bold cyan]  "
        "[dim](atlas config <key> <value>)[/dim]")
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
            exp = Exploration.from_dict(entry)
            display_exploration(exp, history=history)
            return
    click.echo(f"'{exploration_id}' not found. Run 'atlas history'.")


@cli.command("journey")
def journey_cmd():
    """View your exploration journey map."""
    display_journey(load_history())


if __name__ == "__main__":
    cli()
