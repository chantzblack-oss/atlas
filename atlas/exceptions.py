"""Atlas exception hierarchy.

All atlas-specific errors inherit from AtlasError, enabling callers
to catch broad or narrow failure classes without coupling to implementation
details.  CLI layers catch AtlasError; embedding code can catch specific
subclasses for granular recovery.
"""
from __future__ import annotations


class AtlasError(Exception):
    """Base exception for all Atlas errors."""


# -- Engine errors -------------------------------------------------------


class EngineError(AtlasError):
    """Raised when the exploration engine fails."""


class SubprocessError(EngineError):
    """Raised when the Claude CLI subprocess fails.

    Attributes:
        returncode: Exit code from the process (None if it never started).
        stderr: Captured stderr text.
    """

    def __init__(self, message: str, returncode: int | None = None,
                 stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(message)


class ExplorationTimeout(EngineError):
    """Raised when an exploration exceeds the configured timeout.

    Attributes:
        timeout_seconds: The timeout that was exceeded.
    """

    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Exploration timed out after {timeout_seconds} seconds"
        )


class ResponseParseError(EngineError):
    """Raised when the Claude CLI response cannot be parsed.

    Attributes:
        raw_output: The first 500 chars of unparseable output.
    """

    def __init__(self, message: str, raw_output: str = ""):
        self.raw_output = raw_output
        super().__init__(message)


class CLINotFoundError(EngineError):
    """Raised when the 'claude' CLI binary is not found on PATH."""


# -- History errors -------------------------------------------------------


class HistoryError(AtlasError):
    """Raised on history file I/O failures."""


class HistoryCorruptError(HistoryError):
    """Raised when the history file contains invalid data.

    Attributes:
        path: Path to the corrupt file.
        cause: The underlying parse exception.
    """

    def __init__(self, path: str, cause: Exception | None = None):
        self.path = path
        self.cause = cause
        super().__init__(f"Corrupt history file: {path}")


# -- Config errors -------------------------------------------------------


class ConfigError(AtlasError):
    """Raised when configuration loading or validation fails.

    Attributes:
        path: Path to the problematic config file (if any).
    """

    def __init__(self, message: str, path: str | None = None):
        self.path = path
        super().__init__(message)


# -- Input errors -------------------------------------------------------


class InputValidationError(AtlasError):
    """Raised when user input fails sanitization checks."""
