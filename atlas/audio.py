import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

ATLAS_DIR = Path.home() / ".atlas"
AUDIO_DIR = ATLAS_DIR / "audio"

console = Console(stderr=True)

VOICES = {
    "andrew": "en-US-AndrewNeural",
    "brian": "en-US-BrianNeural",
    "ava": "en-US-AvaNeural",
    "emma": "en-US-EmmaNeural",
    "aria": "en-US-AriaNeural",
    "chris": "en-US-ChristopherNeural",
    "ana": "en-US-AnaNeural",
}


def _strip_markdown(text: str) -> str:
    """Convert markdown to speech-friendly plain text."""
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'^[>|]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-=\u2500\u2501]{3,}$', '', text, flags=re.MULTILINE)
    text = re.sub(r'```\w*\n?', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _play(filepath: str):
    """Play audio using the best available method."""
    if shutil.which("mpv"):
        subprocess.run(["mpv", "--no-video", "--really-quiet", filepath])
    elif shutil.which("ffplay"):
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             filepath])
    elif sys.platform == "win32":
        os.startfile(filepath)
    elif sys.platform == "darwin":
        subprocess.run(["afplay", filepath])
    else:
        subprocess.run(["xdg-open", filepath])


# -- Solo TTS (the Atlas podcast voice) ----------------------------------


def _build_solo_script(exploration) -> str:
    """Build narration script with natural pacing for TTS."""
    body = _strip_markdown(exploration.narrative)
    parts = [f"{exploration.title}."]
    parts.extend(p.strip() for p in body.split("\n\n") if p.strip())

    # Thread teasers — natural podcast ending
    threads = getattr(exploration, "next_threads", [])
    if not threads:
        single = getattr(exploration, "next_thread", "")
        if single:
            threads = [single]

    if threads:
        parts.append("And that leaves us with a few questions worth chasing.")
        for t in threads[:3]:
            clean = re.sub(r'^Thread \d+:\s*', '', t)
            parts.append(clean)
        parts.append("Until next time.")

    return "\n\n".join(parts)


async def _solo_generate(text, output, voice):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate="-5%")
    await communicate.save(output)


def play_solo(exploration, voice: str = "en-US-AndrewNeural"):
    """Generate and play single-voice TTS narration."""
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        console.print("[bold red]edge-tts not installed.[/bold red]")
        console.print("[dim]Install: py -m pip install edge-tts[/dim]")
        return

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    output = str(AUDIO_DIR / f"{exploration.id}_solo.mp3")

    if not Path(output).exists():
        script = _build_solo_script(exploration)
        resolved = VOICES.get(voice.lower(), voice)
        with Progress(
            SpinnerColumn("dots"),
            TextColumn("[dim cyan]Generating audio...[/dim cyan]"),
            TextColumn("[dim]\u00b7[/dim]"),
            TimeElapsedColumn(),
            console=console, transient=True,
        ) as progress:
            progress.add_task("", total=None)
            asyncio.run(_solo_generate(script, output, resolved))

    size_kb = Path(output).stat().st_size / 1024
    est_min = size_kb / 16 / 60  # rough MP3 duration estimate
    console.print(
        f"  [dim cyan]Playing:[/dim cyan] [bold]{exploration.title}[/bold]  "
        f"[dim]~{est_min:.0f} min[/dim]"
    )
    _play(output)


def list_voices():
    console.print("\n  [bold cyan]Voices[/bold cyan]\n")
    for alias, full in sorted(VOICES.items()):
        gender = "M" if alias in ("andrew", "brian", "chris") else "F"
        console.print(
            f"  [bold]{alias:10s}[/bold] [dim]{full} ({gender})[/dim]")
    console.print(
        "\n  [dim]atlas config voice <name>  to change voice[/dim]\n")
