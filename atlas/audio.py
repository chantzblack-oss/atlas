import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

ATLAS_DIR = Path.home() / ".atlas"
AUDIO_DIR = ATLAS_DIR / "audio"

console = Console(stderr=True)

HOST_A_VOICE = "en-US-AndrewNeural"
HOST_B_VOICE = "en-US-EmmaNeural"

VOICES = {
    "andrew": "en-US-AndrewNeural",
    "brian": "en-US-BrianNeural",
    "ava": "en-US-AvaNeural",
    "emma": "en-US-EmmaNeural",
    "aria": "en-US-AriaNeural",
    "chris": "en-US-ChristopherNeural",
    "ana": "en-US-AnaNeural",
}

PODCAST_PROMPT = """\
You are the best podcast script writer alive. Your job: take a research \
article and turn it into a two-host conversation so compelling that \
someone does a U-turn in their car to keep listening.

THE HOSTS:
- HOST A: Did the research. Passionate, loves building to a revelation. \
Drops facts like bombs — specific names, dates, numbers. Knows when to \
pause for effect.
- HOST B: Brilliant but hearing this fresh. Asks the exact question the \
listener is screaming in their head. Genuine reactions — disbelief, \
fascination, connecting dots out loud. Sometimes pushes back. Sometimes \
just goes "...wait. Say that again."

RULES FOR GREAT AUDIO:
- This is a CONVERSATION, not a lecture. Both hosts contribute substance.
- Open with a hook so good someone puts their phone down. HOST A drops \
something wild, HOST B reacts honestly.
- Short sentences. Contractions. Natural rhythm. People don't talk in \
paragraphs — they interrupt, they trail off, they circle back.
- HOST B is NOT a yes-man. They ask "but wait, how is that possible?", \
"okay but what does that actually mean?", "that sounds like BS though?"
- Preserve ALL facts, names, dates, and numbers from the source article. \
This is research-backed entertainment.
- Build to revelations. Don't dump everything at once. Let HOST B pull \
it out of HOST A.
- Include at least one moment where both hosts go quiet for a beat — \
the "holy shit" moment when the implication lands.
- End with the cliffhanger thread. HOST A teases it, HOST B says \
something like "okay you HAVE to do that one next."
- Aim for 4-6 minutes of audio (~900-1200 words total).

FORMAT — every line must be exactly:
[A] dialogue here
[B] dialogue here

No stage directions. No parentheticals. No markdown. No narration. \
Just [A] and [B] lines. The performances live in the WORDS, not in \
brackets telling someone how to say them.
"""


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


def _parse_script(text: str) -> list[tuple[str, str]]:
    """Parse podcast script into (speaker, line) tuples."""
    lines = []
    for raw in text.strip().split("\n"):
        raw = raw.strip()
        if raw.startswith("[A]"):
            lines.append(("A", raw[3:].strip()))
        elif raw.startswith("[B]"):
            lines.append(("B", raw[3:].strip()))
    return lines


def _generate_script(exploration, model: str = "opus") -> list[tuple[str, str]]:
    """Use Claude to write a dual-host podcast script."""
    narrative = _strip_markdown(exploration.narrative)
    thread = exploration.next_thread or ""

    user_msg = f"Convert this article into a podcast.\n\nTitle: {exploration.title}\n\n{narrative}"
    if thread:
        user_msg += f"\n\nNext thread (use this as the cliffhanger ending): {thread}"

    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--system-prompt", PODCAST_PROMPT,
        "--tools", "",
        "--no-session-persistence",
    ]

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    result = subprocess.run(
        cmd, input=user_msg,
        capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=180,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Script generation failed: {result.stderr[:200]}")

    data = json.loads(result.stdout)
    if data.get("is_error"):
        raise RuntimeError(data.get("result", "Unknown error"))

    return _parse_script(data.get("result", ""))


async def _render_clips(script, temp_dir, voice_a, voice_b):
    """Generate audio clips for each line, with limited concurrency."""
    import edge_tts

    sem = asyncio.Semaphore(5)
    paths = []

    async def render_one(idx, speaker, text):
        voice = voice_a if speaker == "A" else voice_b
        path = os.path.join(temp_dir, f"{idx:04d}.mp3")
        async with sem:
            communicate = edge_tts.Communicate(text, voice, rate="-3%")
            await communicate.save(path)
        return path

    tasks = [
        render_one(i, speaker, text)
        for i, (speaker, text) in enumerate(script)
    ]
    paths = await asyncio.gather(*tasks)
    return sorted(paths)


def _concat_mp3(clip_paths: list[str], output: str):
    """Concatenate MP3 files into a single file."""
    with open(output, "wb") as out:
        for path in clip_paths:
            with open(path, "rb") as clip:
                out.write(clip.read())


def _play(filepath: str):
    """Play audio using the best available method."""
    if shutil.which("mpv"):
        subprocess.run(["mpv", "--no-video", "--really-quiet", filepath])
    elif shutil.which("ffplay"):
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath])
    elif sys.platform == "win32":
        os.startfile(filepath)
    elif sys.platform == "darwin":
        subprocess.run(["afplay", filepath])
    else:
        subprocess.run(["xdg-open", filepath])


PODCAST_PHASES = [
    (0, "Writing the script..."),
    (10, "Hosts are riffing..."),
    (25, "Recording..."),
    (45, "Almost done recording..."),
    (80, "Mixing the episode..."),
]


def generate_podcast(exploration, model: str = "opus",
                     voice_a: str = HOST_A_VOICE,
                     voice_b: str = HOST_B_VOICE) -> str:
    """Generate a dual-host podcast episode. Returns the MP3 path."""
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        console.print(
            "[bold red]edge-tts not installed.[/bold red]\n"
            "[dim]Install: python -m pip install edge-tts[/dim]")
        sys.exit(1)

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    output = str(AUDIO_DIR / f"{exploration.id}_podcast.mp3")

    if Path(output).exists():
        return output

    # Phase tracking for progress display
    stop = threading.Event()
    result_holder = [None, None]  # [output_path, error]

    def do_work():
        try:
            # 1. Generate script
            script = _generate_script(exploration, model=model)
            if not script:
                result_holder[1] = "Failed to generate script"
                return

            # 2. Render audio clips
            temp_dir = tempfile.mkdtemp(prefix="atlas_podcast_")
            try:
                clip_paths = asyncio.run(
                    _render_clips(script, temp_dir, voice_a, voice_b))

                # 3. Concatenate
                _concat_mp3(clip_paths, output)
                result_holder[0] = output
            finally:
                # Cleanup temp clips
                for f in Path(temp_dir).glob("*.mp3"):
                    f.unlink()
                Path(temp_dir).rmdir()
        except Exception as e:
            result_holder[1] = str(e)
        finally:
            stop.set()

    thread = threading.Thread(target=do_work, daemon=True)

    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[dim cyan]{task.description}[/dim cyan]"),
        TextColumn("[dim]\u00b7[/dim]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(PODCAST_PHASES[0][1], total=None)
        thread.start()
        start = time.time()
        phase_idx = 0
        while not stop.wait(0.3):
            elapsed = time.time() - start
            while (phase_idx < len(PODCAST_PHASES) - 1
                   and elapsed >= PODCAST_PHASES[phase_idx + 1][0]):
                phase_idx += 1
            progress.update(task, description=PODCAST_PHASES[phase_idx][1])

    thread.join()

    if result_holder[1]:
        console.print(f"[bold red]Podcast failed:[/bold red] {result_holder[1]}")
        sys.exit(1)

    return result_holder[0]


def play_podcast(exploration, model: str = "opus",
                 voice_a: str = HOST_A_VOICE,
                 voice_b: str = HOST_B_VOICE):
    """Generate (if needed) and play a dual-host podcast."""
    output = generate_podcast(exploration, model, voice_a, voice_b)

    size_kb = Path(output).stat().st_size / 1024
    est_min = size_kb / 16 / 60  # rough MP3 duration estimate
    console.print(
        f"  [dim cyan]Playing:[/dim cyan] [bold]{exploration.title}[/bold]  "
        f"[dim]~{est_min:.0f} min[/dim]"
    )
    console.print(f"  [dim bright_black]{output}[/dim bright_black]")
    console.print()

    _play(output)


# ── Solo TTS (quick read) ──────────────────────────────────

def _build_solo_script(exploration) -> str:
    """Plain narration script for solo TTS."""
    body = _strip_markdown(exploration.narrative)
    parts = [f"{exploration.title}."]
    parts.extend(p.strip() for p in body.split("\n\n") if p.strip())
    if exploration.next_thread:
        parts.append(f"Next thread. {exploration.next_thread}")
    return "\n\n".join(parts)


async def _solo_generate(text, output, voice):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate="-5%")
    await communicate.save(output)


def play_solo(exploration, voice: str = "en-US-AndrewNeural"):
    """Quick single-voice TTS read."""
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        console.print("[bold red]edge-tts not installed.[/bold red]")
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

    console.print(f"  [dim cyan]Playing:[/dim cyan] {exploration.title}")
    _play(output)


def list_voices():
    console.print("\n  [bold cyan]Voices[/bold cyan]\n")
    for alias, full in sorted(VOICES.items()):
        gender = "M" if alias in ("andrew", "brian", "chris") else "F"
        console.print(f"  [bold]{alias:10s}[/bold] [dim]{full} ({gender})[/dim]")
    console.print(
        "\n  [dim]Podcast uses Andrew + Emma by default[/dim]"
        "\n  [dim]atlas config voice <name>  to change solo voice[/dim]\n")
