import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .prompts import build_system_prompt
from .history import (
    load_history, save_exploration, find_connections,
    format_history_context, get_recent_titles,
)

console = Console(stderr=True)

MODEL = "opus"

STATUS_PHASES = [
    (0, "Launching exploration..."),
    (3, "Falling down the rabbit hole..."),
    (8, "Searching for what most people miss..."),
    (18, "Digging through primary sources..."),
    (35, "Cross-referencing the evidence..."),
    (55, "Crafting the narrative..."),
    (90, "Going deep \u2014 this is a thorough one..."),
    (150, "Still going... must have found something fascinating"),
]


@dataclass
class Exploration:
    id: str
    timestamp: str
    mode: str
    input_text: str | None
    title: str
    narrative: str
    tags: list[str]
    sources: list[dict]
    next_thread: str
    connections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_sources(text: str) -> list[dict]:
    """Extract markdown links as sources from the narrative."""
    sources = []
    seen = set()
    for match in re.finditer(r'\[([^\]]+)\]\((https?://[^)]+)\)', text):
        title, url = match.group(1), match.group(2)
        if url not in seen:
            seen.add(url)
            sources.append({"url": url, "title": title})
    return sources


def _parse_metadata(text: str) -> tuple[str, dict]:
    """Split narrative from trailing atlas-meta JSON block."""
    pattern = r"```atlas-meta\s*\n(.+?)\n\s*```\s*$"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        narrative = text[:match.start()].strip()
        try:
            return narrative, json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    pattern2 = r"```(?:json)?\s*\n(\{.+?\})\n\s*```\s*$"
    match2 = re.search(pattern2, text, re.DOTALL)
    if match2:
        narrative = text[:match2.start()].strip()
        try:
            return narrative, json.loads(match2.group(1))
        except json.JSONDecodeError:
            pass

    try:
        data = json.loads(text)
        if isinstance(data, dict) and "narrative" in data:
            return data["narrative"], data
    except json.JSONDecodeError:
        pass

    return text, {
        "title": "Untitled Exploration",
        "tags": [],
        "next_thread": "",
        "connections": [],
    }


def _run_with_progress(cmd, user_message, env):
    """Run claude CLI with animated progress phases and elapsed time."""
    stop = threading.Event()
    result = [None]
    error = [None]

    def run():
        try:
            result[0] = subprocess.run(
                cmd, input=user_message,
                capture_output=True, encoding="utf-8", errors="replace",
                env=env, timeout=300,
            )
        except subprocess.TimeoutExpired:
            error[0] = "Exploration timed out after 5 minutes."
        except Exception as e:
            error[0] = str(e)
        finally:
            stop.set()

    thread = threading.Thread(target=run, daemon=True)

    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[dim cyan]{task.description}[/dim cyan]"),
        TextColumn("[dim]\u00b7[/dim]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(STATUS_PHASES[0][1], total=None)
        thread.start()
        start = time.time()
        phase_idx = 0
        while not stop.wait(0.3):
            elapsed = time.time() - start
            while (phase_idx < len(STATUS_PHASES) - 1
                   and elapsed >= STATUS_PHASES[phase_idx + 1][0]):
                phase_idx += 1
            progress.update(task, description=STATUS_PHASES[phase_idx][1])

    thread.join()

    if error[0]:
        console.print(f"[bold red]{error[0]}[/bold red]")
        sys.exit(1)

    return result[0]


def explore(mode: str, user_input: str | None = None,
            angle: str | None = None,
            model: str = MODEL) -> tuple["Exploration", list[dict]]:
    """Run an exploration. Returns (Exploration, history) for display."""
    history = load_history()

    system_prompt = build_system_prompt(
        mode=mode,
        user_input=user_input,
        angle=angle,
        history_context=format_history_context(history),
        recent_titles=get_recent_titles(history) if mode == "surprise" else None,
    )

    if mode == "surprise":
        user_message = "Surprise me. Find something fascinating."
    elif mode == "thread":
        user_message = f"Pull this thread: {user_input}"
    else:
        msg = f"Go deep on: {user_input}"
        if angle:
            msg += f"\nAngle: {angle}"
        user_message = msg

    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--system-prompt", system_prompt,
        "--allowedTools", "WebSearch,WebFetch",
        "--no-session-persistence",
    ]

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    result = _run_with_progress(cmd, user_message, env)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        console.print(f"[bold red]Exploration failed.[/bold red]\n{stderr}")
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        console.print("[bold red]Failed to parse response.[/bold red]")
        console.print(result.stdout[:500])
        sys.exit(1)

    if data.get("is_error"):
        console.print(
            f"[bold red]Error:[/bold red] {data.get('result', 'unknown')}"
        )
        sys.exit(1)

    raw_text = data.get("result", "")
    narrative, meta = _parse_metadata(raw_text)
    sources = _extract_sources(narrative)

    exploration_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()

    claude_connections = meta.get("connections", [])
    tag_connections = find_connections(meta.get("tags", []), history)
    all_connections = list(set(claude_connections + tag_connections))

    exploration = Exploration(
        id=exploration_id,
        timestamp=now,
        mode=mode,
        input_text=user_input,
        title=meta.get("title", "Untitled"),
        narrative=narrative,
        tags=meta.get("tags", []),
        sources=sources or [],
        next_thread=meta.get("next_thread", ""),
        connections=all_connections,
    )

    save_exploration(exploration.to_dict())
    return exploration, history
