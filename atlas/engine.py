"""Atlas exploration engine.

Drives the Claude CLI to produce researched, narrative explorations.
All failures raise typed exceptions from atlas.exceptions rather than
calling sys.exit(), making the engine fully testable and embeddable.

Security model (ref: https://semgrep.dev/docs/cheat-sheets/python-command-injection):
- Command built as a list -- never shell-interpolated
- User input sanitized: null bytes stripped, control chars removed,
  Unicode NFC-normalized, length-bounded
- shlex.quote() deliberately NOT used: it is POSIX-only and unsafe on
  Windows cmd.exe (ref: https://github.com/python/cpython/pull/21502).
  List-based subprocess.run(shell=False) is the correct mitigation.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from rich.console import Console
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, TimeElapsedColumn,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
    before_sleep_log,
)

from .config import load_config
from .exceptions import (
    CLINotFoundError,
    ExplorationTimeout,
    InputValidationError,
    ResponseParseError,
    SubprocessError,
)
from .history import (
    load_history, save_exploration, find_connections,
    format_history_context, format_journey_context,
    get_recent_titles,
)
from .prompts import build_system_prompt

logger = logging.getLogger("atlas.engine")
console = Console(stderr=True)

# Backward-compat default; prefer config.engine.model at runtime
MODEL = "opus"

STYLES = ["story", "myth-buster", "scale"]

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


# -- Input sanitization --------------------------------------------------

_CONTROL_CHARS = set(range(0x00, 0x20)) - {0x09, 0x0A, 0x0D}
_MAX_INPUT_BYTES = 8_000


def sanitize_input(text: str, max_length: int = 2_000,
                   field_name: str = "input") -> str:
    """Validate and sanitize user-supplied text."""
    if not isinstance(text, str):
        raise InputValidationError(f"{field_name} must be a string")
    text = text.replace(chr(0), "")
    text = "".join(ch for ch in text if ord(ch) not in _CONTROL_CHARS)
    text = unicodedata.normalize("NFC", text)
    if len(text) > max_length:
        raise InputValidationError(
            f"{field_name} exceeds maximum length of {max_length:,} characters"
        )
    if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
        raise InputValidationError(
            f"{field_name} exceeds maximum byte size of {_MAX_INPUT_BYTES:,}"
        )
    stripped = text.strip()
    if not stripped:
        raise InputValidationError(f"{field_name} must not be empty")
    return stripped


def _validate_system_prompt(prompt: str, max_length: int = 50_000) -> str:
    """Validate the assembled system prompt before passing to the CLI."""
    if len(prompt) > max_length:
        logger.warning(
            "System prompt length (%d) exceeds limit (%d); truncating",
            len(prompt), max_length,
        )
        prompt = prompt[:max_length]
    return prompt.replace(chr(0), "")


# -- Data types ----------------------------------------------------------


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
    next_threads: list[str] = field(default_factory=list)
    style: str | None = None
    connections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Exploration:
        """Build an Exploration from a history dict, filling missing fields."""
        defaults = {
            "next_threads": [],
            "style": None,
            "connections": [],
        }
        kwargs = {}
        for f in cls.__dataclass_fields__:
            if f in data:
                kwargs[f] = data[f]
            elif f in defaults:
                kwargs[f] = defaults[f]
            else:
                kwargs[f] = None
        return cls(**kwargs)


# -- Parsing -------------------------------------------------------------


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
        "next_threads": [],
        "connections": [],
    }


# -- Subprocess execution -----------------------------------------------


def _run_with_progress(cmd, user_message, env, timeout=300):
    """Run claude CLI with animated progress phases and elapsed time."""
    stop = threading.Event()
    result = [None]
    error = [None]

    def run():
        try:
            result[0] = subprocess.run(
                cmd, input=user_message,
                capture_output=True, encoding="utf-8", errors="replace",
                env=env, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            error[0] = ExplorationTimeout(timeout)
        except FileNotFoundError:
            error[0] = CLINotFoundError(
                "'claude' CLI not found on PATH. "
                "Install: npm install -g @anthropic-ai/claude-code"
            )
        except Exception as e:
            error[0] = SubprocessError(str(e))
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
        raise error[0]

    return result[0]


def _call_claude(cmd, user_message, env, engine_config):
    """Call Claude CLI with retry logic for transient failures."""

    @retry(
        retry=retry_if_exception_type(SubprocessError),
        stop=stop_after_attempt(engine_config.max_retries),
        wait=wait_exponential_jitter(
            initial=engine_config.retry_base_delay,
            max=engine_config.retry_max_delay,
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _attempt():
        result = _run_with_progress(
            cmd, user_message, env, engine_config.timeout)
        if result.returncode != 0:
            raise SubprocessError(
                f"Claude CLI exited with code {result.returncode}",
                returncode=result.returncode,
                stderr=result.stderr.strip(),
            )
        return result

    return _attempt()


# -- Main exploration function ------------------------------------------


def explore(mode: str, user_input: str | None = None,
            angle: str | None = None,
            style: str | None = None,
            model: str | None = None) -> tuple[Exploration, list[dict]]:
    """Run an exploration. Returns (Exploration, history) for display.

    Args:
        mode: surprise, thread, deep, or podcast.
        user_input: Topic or thread text.
        angle: Specific angle for deep dives.
        style: Storytelling style -- story, myth-buster, or scale.
        model: Claude model override.

    Raises:
        InputValidationError: If user input fails sanitization.
        CLINotFoundError: If the claude CLI is not on PATH.
        ExplorationTimeout: If the exploration exceeds timeout.
        SubprocessError: If the CLI process fails after retries.
        ResponseParseError: If the CLI response cannot be parsed.
    """
    config = load_config()
    engine = config.engine
    model = model or engine.model

    # Sanitize user input
    if user_input is not None:
        user_input = sanitize_input(
            user_input, max_length=engine.max_input_length)
    if angle is not None:
        angle = sanitize_input(
            angle, max_length=engine.max_input_length, field_name="angle")

    # Verify claude CLI exists
    if not shutil.which("claude"):
        raise CLINotFoundError(
            "'claude' CLI not found on PATH. "
            "Install: npm install -g @anthropic-ai/claude-code"
        )

    history = load_history()

    # Build prompts with journey awareness
    needs_recent = (
        mode == "surprise"
        or (mode == "podcast" and user_input is None)
    )
    journey_context = format_journey_context(history)

    system_prompt = build_system_prompt(
        mode=mode,
        user_input=user_input,
        angle=angle,
        style=style,
        history_context=format_history_context(history),
        recent_titles=get_recent_titles(history) if needs_recent else None,
        journey_context=journey_context,
    )
    system_prompt = _validate_system_prompt(
        system_prompt, engine.max_prompt_length)

    # Build user message
    if mode == "surprise":
        user_message = "Surprise me. Find something fascinating."
    elif mode == "thread":
        user_message = f"Pull this thread: {user_input}"
    elif mode == "podcast":
        if user_input:
            user_message = f"Create a podcast episode about: {user_input}"
        else:
            user_message = (
                "Surprise me. Pick something fascinating for a podcast episode."
            )
    else:  # deep
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

    # Execute with retry
    result = _call_claude(cmd, user_message, env, engine)

    # Parse response
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ResponseParseError(
            "Failed to parse Claude response as JSON",
            raw_output=result.stdout[:500],
        )

    if data.get("is_error"):
        raise SubprocessError(
            data.get("result", "Unknown Claude error"),
            returncode=0,
            stderr=data.get("result", ""),
        )

    raw_text = data.get("result", "")
    narrative, meta = _parse_metadata(raw_text)
    sources = _extract_sources(narrative)

    exploration_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()

    # Multi-thread: prefer next_threads array, fall back to single
    next_threads = meta.get("next_threads", [])
    if not next_threads and meta.get("next_thread"):
        next_threads = [meta["next_thread"]]
    next_thread = next_threads[0] if next_threads else ""

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
        next_thread=next_thread,
        next_threads=next_threads,
        style=style,
        connections=all_connections,
    )

    save_exploration(exploration.to_dict())
    return exploration, history
