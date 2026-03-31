import sys

import click

from .engine import explore, MODEL
from .history import load_history, get_all_threads, search_history
from .display import (
    display_exploration, display_history,
    display_threads, display_search_results,
)


@click.group(invoke_without_command=True)
@click.option("--model", "-m", default=MODEL,
              help="Model: opus, sonnet, haiku")
@click.option("--listen", "-l", is_flag=True,
              help="Play as podcast after exploring")
@click.option("--voice", "-v", default="andrew",
              help="TTS voice: andrew, brian, ava, emma, aria, chris")
@click.pass_context
def cli(ctx, model, listen, voice):
    """ATLAS \u2014 a pocket Veritasium / Kurzgesagt.

    Run with no arguments for a surprise exploration.

    \b
    Commands:
      atlas                  surprise me
      atlas thread "idea"    pull a thread
      atlas deep "topic"     deep dive
      atlas next             follow the last thread
      atlas listen           play last exploration as podcast
      atlas history          browse past explorations
    """
    ctx.ensure_object(dict)
    ctx.obj["model"] = model
    ctx.obj["listen"] = listen
    ctx.obj["voice"] = voice
    if ctx.invoked_subcommand is None:
        ctx.invoke(surprise_cmd)


def _maybe_listen(ctx, exploration):
    """Play audio if --listen flag was set."""
    if ctx.obj.get("listen"):
        from .audio import play_exploration
        play_exploration(exploration, voice=ctx.obj.get("voice", "andrew"))


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
    idea_text = " ".join(idea)
    result, history = explore(mode="thread", user_input=idea_text,
                              model=ctx.obj["model"])
    display_exploration(result, history=history)
    _maybe_listen(ctx, result)


@cli.command("deep")
@click.argument("topic", nargs=-1, required=True)
@click.option("--angle", "-a", help="A specific angle or question to pursue")
@click.pass_context
def deep_cmd(ctx, topic, angle):
    """Kurzgesagt-style deep dive with real sources.

    Example: atlas deep "CRISPR" --angle "off-target effects"
    """
    topic_text = " ".join(topic)
    result, history = explore(mode="deep", user_input=topic_text,
                              angle=angle, model=ctx.obj["model"])
    display_exploration(result, history=history)
    _maybe_listen(ctx, result)


@cli.command("next")
@click.argument("exploration_id", required=False)
@click.pass_context
def next_cmd(ctx, exploration_id):
    """Follow the thread from your last (or specified) exploration.

    \b
    Examples:
      atlas next            follow the most recent thread
      atlas next 6f8e6250   follow a specific exploration's thread
    """
    history = load_history()
    if not history:
        click.echo("No explorations yet. Run 'atlas' to start.")
        return

    if exploration_id:
        entry = next((e for e in history if e["id"] == exploration_id), None)
        if not entry:
            click.echo(f"Exploration '{exploration_id}' not found. "
                       "Run 'atlas history' to see IDs.")
            return
    else:
        entry = history[-1]

    thread = entry.get("next_thread", "")
    if not thread:
        click.echo(f"Exploration '{entry['id']}' has no next thread.")
        return

    result, hist = explore(mode="thread", user_input=thread,
                           model=ctx.obj["model"])
    display_exploration(result, history=hist)
    _maybe_listen(ctx, result)


@cli.command("listen")
@click.argument("exploration_id", required=False)
@click.option("--voice", "-v", default="andrew",
              help="TTS voice: andrew, brian, ava, emma, aria, chris")
@click.pass_context
def listen_cmd(ctx, exploration_id, voice):
    """Play an exploration as a podcast.

    \b
    Examples:
      atlas listen              play the most recent exploration
      atlas listen a43aef79     play a specific exploration
      atlas listen --voice emma play with a different voice
    """
    from .audio import play_exploration, list_voices

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
            click.echo(f"Exploration '{exploration_id}' not found.")
            return
    else:
        entry = history[-1]

    from .engine import Exploration
    exp = Exploration(
        **{k: entry[k] for k in Exploration.__dataclass_fields__}
    )
    play_exploration(exp, voice=voice)


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
                **{k: entry[k] for k in Exploration.__dataclass_fields__}
            )
            display_exploration(exp, history=history)
            return
    click.echo(f"Exploration '{exploration_id}' not found. "
               "Run 'atlas history' to see IDs.")


if __name__ == "__main__":
    cli()
