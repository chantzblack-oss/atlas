import json
import re
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import anthropic
from rich.console import Console

from .prompts import build_system_prompt
from .history import (
    load_history, save_exploration, find_connections,
    format_history_context, get_recent_titles,
)

console = Console(stderr=True)

MODEL = "claude-opus-4-6"
MAX_TOKENS = 16000
THINKING_BUDGET = 10000


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


def _extract_sources(response) -> list[dict]:
    sources = []
    seen_urls = set()
    for block in response.content:
        if block.type == "web_search_tool_result":
            for result in getattr(block, "search_results", []):
                url = getattr(result, "url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({
                        "url": url,
                        "title": getattr(result, "title", ""),
                    })
    return sources


def _extract_text(response) -> str:
    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "\n".join(parts)


def _parse_metadata(text: str) -> tuple[str, dict]:
    """Split narrative from trailing atlas-meta JSON block."""
    # Look for ```atlas-meta ... ``` at the end
    pattern = r"```atlas-meta\s*\n(.+?)\n\s*```\s*$"
    match = re.search(pattern, text, re.DOTALL)

    if match:
        narrative = text[:match.start()].strip()
        try:
            meta = json.loads(match.group(1))
            return narrative, meta
        except json.JSONDecodeError:
            pass

    # Fallback: look for any trailing JSON block
    pattern2 = r"```(?:json)?\s*\n(\{.+?\})\n\s*```\s*$"
    match2 = re.search(pattern2, text, re.DOTALL)
    if match2:
        narrative = text[:match2.start()].strip()
        try:
            meta = json.loads(match2.group(1))
            return narrative, meta
        except json.JSONDecodeError:
            pass

    # Last resort: try to parse the entire text as JSON (legacy format)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "narrative" in data:
            return data["narrative"], data
    except json.JSONDecodeError:
        pass

    # Total fallback
    return text, {
        "title": "Untitled Exploration",
        "tags": [],
        "next_thread": "",
        "connections": [],
    }


def _check_api_key():
    """Verify ANTHROPIC_API_KEY is set before making calls."""
    import os
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        console.print(
            "[bold red]Missing ANTHROPIC_API_KEY.[/bold red]\n"
            "Set it with: [cyan]set ANTHROPIC_API_KEY=sk-ant-...[/cyan]\n"
            "Get yours at: [link=https://console.anthropic.com/settings/keys]"
            "console.anthropic.com/settings/keys[/link]"
        )
        sys.exit(1)


STATUS_MESSAGES = {
    "thinking": "[dim cyan]Thinking deeply...[/dim cyan]",
    "searching": "[dim cyan]Searching {n}...[/dim cyan]",
    "reading": "[dim cyan]Reading sources...[/dim cyan]",
    "writing": "[dim cyan]Writing...[/dim cyan]",
}


def explore(mode: str, user_input: str | None = None,
            angle: str | None = None) -> Exploration:
    _check_api_key()

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

    client = anthropic.Anthropic()

    # Stream the response for live progress feedback
    with console.status(
        "[bold cyan]Launching exploration...", spinner="dots"
    ) as status:
        search_count = 0
        has_text = False

        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 15,
                },
            ],
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    block_type = getattr(block, "type", "")
                    if block_type == "thinking":
                        status.update(STATUS_MESSAGES["thinking"])
                    elif block_type == "web_search_tool_use":
                        search_count += 1
                        status.update(
                            STATUS_MESSAGES["searching"].format(n=search_count)
                        )
                    elif block_type == "web_search_tool_result":
                        status.update(STATUS_MESSAGES["reading"])
                    elif block_type == "text" and not has_text:
                        has_text = True
                        status.update(STATUS_MESSAGES["writing"])

            response = stream.get_final_message()

    # Extract and parse
    sources = _extract_sources(response)
    raw_text = _extract_text(response)
    narrative, meta = _parse_metadata(raw_text)

    exploration_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()

    # Merge connections from Claude + tag-based matching
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
    return exploration
