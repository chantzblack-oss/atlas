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
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import AtlasConfig, EngineConfig, load_config
from .exceptions import (
    CLINotFoundError,
    ExplorationTimeout,
    InputValidationError,
    ResponseParseError,
    SubprocessError,
)
from .history import (
    load_history,
    save_exploration,
    find_connections,
    format_history_context,
    get_recent_titles,
)
from .prompts import build_system_prompt

logger = logging.getLogger("atlas.engine")
console = Console(stderr=True)

# Backward-compat default; prefer config.engine.model at runtime
MODEL = "opus"

STATUS_PHASES = [
    (0, "Launching exploration..."),
    (3, "Falling down the rabbit hole..."),
    (8, "Searching for what most people miss..."),
    (18, "Digging through primary sources..."),
    (35, "Cross-referencing the evidence..."),
    (55, "Crafting the narrative..."),
    (90, "Going deep — this is a thorough one..."),
    (150, "Still going... must have found something fascinating"),
]


# -- Input sanitization --------------------------------------------------

# ASCII control chars EXCEPT tab (0x09), linefeed (0x0A), carriage return (0x0D)
_CONTROL_CHARS = set(range(0x00, 0x20)) - {0x09, 0x0A, 0x0D}
_MAX_INPUT_BYTES = 8_000  # safety ceiling for CLI arg byte length


def sanitize_input(text: str, max_length: int = 2_000,
                   field_name: str = "input") -> str:
    """Validate and sanitize user-supplied text.

    Defenses (list-based subprocess args already prevent shell injection):
    - Strip null bytes (can truncate C-level string handling)
    - Remove ASCII control characters (keep tab, LF, CR)
    - Normalize Unicode to NFC (collapse homoglyphs)
    - Enforce character and byte length limits
    - Reject empty / whitespace-only inputs

    Ref: https://securecodingpractices.com/prevent-command-injection-python-subprocess/
    """
    if not isinstance(text, str):
        raise InputValidationError(f"{field_name} must be a string")

    # Strip null bytes
    text = text.replace(chr(0), "")

    # Remove dangerous control characters
    text = "".join(ch for ch in text if ord(ch) not in _CONTROL_CHARS)

    # Normalize Unicode to NFC
    text = unicodedata.normalize("NFC", text)

    # Enforce length limits
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
