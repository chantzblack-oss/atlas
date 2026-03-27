import sys

import click

from .engine import explore
from .history import load_history, get_all_threads, search_history
from .display import (
    display_exploration, display_history,
    display_threads, display_search_results,
)


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """ATLAS - A personal exploration engine.

    Three ways to explore:

      atlas surprise          — let ATLAS pick something fascinating

      atlas thread "idea"     — pull a vague idea into something real

      atlas deep "topic"      — directed deep dive with real sources

    Your explorations are saved. Use 'atlas history' to revisit them.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command("surprise")
def surprise_cmd():
    """Let ATLAS pick something fascinating for you."""
    result = explore(mode="surprise")
    display_exploration(result)


@cli.command("thread")
@click.argument("idea", nargs=-1, required=True)
def thread_cmd(idea):
    """Pull a thread from a vague idea.

    Example: atlas thread "why do hospitals smell like that"
    """
    idea_text = " ".join(idea)
    result = explore(mode="thread", user_input=idea_text)
    display_exploration(result)


@cli.command("deep")
@click.argument("topic", nargs=-1, required=True)
@click.option("--angle", "-a", help="A specific angle or question to pursue")
def deep_cmd(topic, angle):
    """Go deep on a specific topic with real sources.

    Example: atlas deep "CRISPR" --angle "off-target effects"
    """
    topic_text = " ".join(topic)
    result = explore(mode="deep", user_input=topic_text, angle=angle)
    display_exploration(result)


@cli.command("history")
def history_cmd():
    """Show past explorations."""
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
            display_exploration(exp)
            return
    click.echo(f"Exploration '{exploration_id}' not found. Run 'atlas history' to see IDs.")


if __name__ == "__main__":
    cli()
