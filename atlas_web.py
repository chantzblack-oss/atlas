#!/usr/bin/env python3
"""
Atlas Web -- Mobile-friendly web interface for Atlas explorations.

Uses the Claude CLI (no API key needed -- uses your existing claude auth).

HOW TO RUN:
1. pip install flask
2. python atlas_web.py
3. Open http://localhost:8080 in your browser
4. For phone access on same wifi: use the IP address shown at startup
"""

import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

try:
    from flask import (
        Flask, request, jsonify, session, redirect, Response, stream_with_context,
    )
except ImportError:
    print("\n  Install Flask first:  pip install flask\n")
    sys.exit(1)

# -- Config -------------------------------------------------------------------
MODEL = "opus"
HISTORY_FILE = Path.home() / ".atlas" / "history.json"
APP_PASSWORD = os.environ.get("ATLAS_PASSWORD", "atlas")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())


# -- Auth ---------------------------------------------------------------------
@app.before_request
def check_auth():
    if request.path == "/login" or request.path.startswith("/static"):
        return None
    if not session.get("authenticated"):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Not logged in"}), 401
        return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == APP_PASSWORD:
            session["authenticated"] = True
            return redirect("/")
        return LOGIN_HTML.replace("{{error}}", '<p class="err">Wrong password.</p>')
    return LOGIN_HTML.replace("{{error}}", "")


# -- Prompts (matching atlas CLI) ---------------------------------------------
BASE_PROMPT = """You are ATLAS -- a pocket Veritasium meets Kurzgesagt, in text. Your job is to deliver the genuine thrill of discovery: the moment something clicks and the world looks different than it did five minutes ago.

NARRATIVE STRUCTURE -- every exploration follows this arc:
1. THE HOOK -- a counterintuitive claim, a stunning number, a paradox. The first sentence must stop someone mid-scroll.
2. THE SETUP -- orient the reader fast. Just enough to understand why they should care.
3. "BUT HERE'S WHERE IT GETS WEIRD" -- the turn. The complication.
4. THE DEEP DIG -- the meat. Real evidence, real people, real numbers. Layer revelations.
5. THE ZOOM OUT -- Pull back. Show how this connects to something bigger about how the world works.
6. THE THREAD -- end with one specific, unanswered question. A cliffhanger.

VOICE: Write like a brilliant obsessive researcher talking to a smart friend at 2am. Use SCALE to create awe. Short paragraphs. Punch. Let revelations breathe.

RESEARCH: Use web search aggressively. Multiple searches, varied queries. Specific names, dates, places, numbers. Cite inline as [source title](url).

WRITING: Markdown. 800-1500 words. Dense, not padded. Every paragraph earns its place.

OUTPUT FORMAT:
Write the full narrative first. Then at the very end, include exactly this:

```atlas-meta
{"title": "3-8 word title", "tags": ["tag1", "tag2", "tag3"], "next_thread": "A specific irresistible question -- the cliffhanger", "connections": []}
```

Title rules: magazine cover, not textbook."""

SURPRISE_ADDITION = """
MODE: SURPRISE ME
Pick something that would make someone put their phone down and say "wait, seriously?" to nobody. Not trivia. Something that genuinely shifts how you see the world. You have the entire span of human knowledge. Don't play it safe."""

THREAD_ADDITION = """
MODE: PULL THIS THREAD
The person is curious about: "{user_input}"

They don't want an explainer or a Wikipedia summary. They want the Veritasium treatment -- take this thread and follow it to the place that makes someone say "WAIT. What?" """

DEEP_ADDITION = """
MODE: GO DEEP
Topic: "{user_input}"
{angle_line}

They know the basics. Give them the Kurzgesagt deep-dive -- take them past the surface to where the real story lives. Actual papers, live debates, specific researchers, original data. Go deeper than any Wikipedia article."""


# -- Claude CLI helpers -------------------------------------------------------
def find_claude_cli():
    """Find the claude CLI binary."""
    path = shutil.which("claude")
    if path:
        return path
    # Check common Windows locations
    for candidate in [
        Path.home() / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
        Path.home() / ".claude" / "bin" / "claude.exe",
    ]:
        if candidate.exists():
            return str(candidate)
    return "claude"  # Hope it's on PATH


CLAUDE_CLI = find_claude_cli()


def build_prompt(mode, user_input=None, angle=None):
    parts = [BASE_PROMPT]
    if mode == "surprise":
        parts.append(SURPRISE_ADDITION)
    elif mode == "thread":
        parts.append(THREAD_ADDITION.format(user_input=user_input))
    elif mode == "deep":
        angle_line = f"Angle: {angle}" if angle else ""
        parts.append(DEEP_ADDITION.format(user_input=user_input, angle_line=angle_line))
    return "\n".join(parts)


def parse_meta(text):
    """Extract atlas-meta JSON block from the end of the narrative."""
    meta = {"title": "Untitled", "tags": [], "next_thread": "", "connections": []}
    # Try atlas-meta block
    pattern = r'```atlas-meta\s*\n(.*?)\n\s*```'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        try:
            meta = {**meta, **json.loads(match.group(1))}
        except json.JSONDecodeError:
            pass
        narrative = text[:match.start()].strip()
    else:
        narrative = text.strip()
    return narrative, meta


def clean_text(text):
    """Remove citation tags from Claude output."""
    text = re.sub(r'<[^>]*cite[^>]*>', '', text)
    text = re.sub(r'<[^>]*antml[^>]*>', '', text)
    text = re.sub(r'</?[a-zA-Z][^>]*>', '', text)
    return text


def run_claude(system_prompt, user_message, mode="surprise"):
    """Run Claude CLI and yield SSE events. No API key needed."""
    cmd = [
        CLAUDE_CLI, "-p",
        "--model", MODEL,
        "--output-format", "json",
        "--system-prompt", system_prompt,
        "--allowedTools", "WebSearch,WebFetch",
        "--no-session-persistence",
    ]

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    # Yield initial status
    yield f"data: {json.dumps({'type': 'status', 'message': 'Launching exploration...'})}\n\n"

    try:
        result = subprocess.run(
            cmd, input=user_message,
            capture_output=True, encoding="utf-8", errors="replace",
            env=env, timeout=300,
        )
    except subprocess.TimeoutExpired:
        yield f"data: {json.dumps({'type': 'error', 'message': 'Exploration timed out after 5 minutes.'})}\n\n"
        return
    except FileNotFoundError:
        yield f"data: {json.dumps({'type': 'error', 'message': 'Claude CLI not found. Make sure claude is installed and on your PATH.'})}\n\n"
        return
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return

    if result.returncode != 0:
        stderr = result.stderr.strip()[:500]
        yield f"data: {json.dumps({'type': 'error', 'message': f'Claude CLI error: {stderr}'})}\n\n"
        return

    # Parse the JSON output
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        # Try to use raw output as text
        data = {"result": result.stdout}

    # Extract text from Claude CLI JSON response
    raw_text = ""
    if isinstance(data, dict):
        raw_text = data.get("result", "") or data.get("text", "") or result.stdout
    elif isinstance(data, list):
        # Claude sometimes returns array of content blocks
        for block in data:
            if isinstance(block, dict) and block.get("type") == "text":
                raw_text += block.get("text", "")
    else:
        raw_text = str(data)

    raw_text = clean_text(raw_text)
    narrative, meta = parse_meta(raw_text)

    # Send the full narrative as text
    yield f"data: {json.dumps({'type': 'text', 'content': narrative})}\n\n"

    # Save to Atlas history
    entry = {
        "id": f"{random.randint(0x10000000, 0xFFFFFFFF):08x}",
        "mode": mode,
        "title": meta["title"],
        "narrative": narrative,
        "tags": meta["tags"],
        "next_thread": meta["next_thread"],
        "connections": meta["connections"],
        "sources": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_history_entry(entry)

    yield f"data: {json.dumps({'type': 'meta', 'meta': meta, 'narrative': narrative, 'id': entry['id']})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


# -- History helpers ----------------------------------------------------------
def load_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_history_entry(entry):
    HISTORY_FILE.parent.mkdir(exist_ok=True)
    history = load_history()
    history.append(entry)
    HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


# -- API routes ---------------------------------------------------------------
@app.route("/api/explore", methods=["POST"])
def api_explore():
    data = request.json
    mode = data.get("mode", "surprise")
    user_input = data.get("input", "").strip()
    angle = data.get("angle", "").strip() or None

    system = build_prompt(mode, user_input=user_input or None, angle=angle)

    if mode == "surprise":
        user_msg = "Surprise me. Find something mind-blowing."
    elif mode == "thread":
        user_msg = f"Pull this thread: {user_input}"
    elif mode == "deep":
        user_msg = f"Deep dive: {user_input}"
        if angle:
            user_msg += f"\nAngle: {angle}"
    else:
        user_msg = user_input or "Surprise me."

    return Response(
        stream_with_context(run_claude(system, user_msg, mode=mode)),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route("/api/next", methods=["POST"])
def api_next():
    data = request.json
    thread = data.get("thread", "")
    if not thread:
        return jsonify({"ok": False, "error": "No thread provided"})

    system = build_prompt("thread", user_input=thread)
    user_msg = f"Pull this thread: {thread}"

    return Response(
        stream_with_context(run_claude(system, user_msg, mode="thread")),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route("/api/history", methods=["GET"])
def api_history():
    history = load_history()
    summaries = []
    for e in reversed(history[-50:]):
        summaries.append({
            "id": e.get("id", ""),
            "mode": e.get("mode", ""),
            "title": e.get("title", ""),
            "tags": e.get("tags", []),
            "next_thread": e.get("next_thread", ""),
            "timestamp": e.get("timestamp", ""),
        })
    return jsonify(summaries)


@app.route("/api/revisit/<exploration_id>", methods=["GET"])
def api_revisit(exploration_id):
    history = load_history()
    for e in history:
        if e.get("id") == exploration_id:
            return jsonify({"ok": True, **e})
    return jsonify({"ok": False, "error": "Not found"})


# -- Main page ----------------------------------------------------------------
@app.route("/")
def index():
    return APP_HTML


# -- HTML templates -----------------------------------------------------------
LOGIN_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0">
<title>Atlas</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{background:#0a0a0f;color:#e0ddd5;font-family:'Georgia',serif;display:flex;align-items:center;justify-content:center;min-height:100vh;}
.box{text-align:center;padding:40px 28px;max-width:340px;width:100%;}
h1{font-size:28px;font-weight:400;letter-spacing:8px;color:#7eb8da;margin-bottom:4px;}
.sub{font-size:9px;letter-spacing:4px;text-transform:uppercase;color:rgba(126,184,218,0.3);margin-bottom:36px;}
input{width:100%;padding:14px 16px;background:rgba(15,15,25,0.8);border:1px solid rgba(126,184,218,0.15);border-radius:4px;color:#e0ddd5;font-size:15px;font-family:Georgia,serif;outline:none;margin-bottom:14px;}
input:focus{border-color:rgba(126,184,218,0.4);}
button{width:100%;padding:13px;background:rgba(126,184,218,0.08);border:1px solid rgba(126,184,218,0.25);border-radius:4px;color:#7eb8da;font-size:12px;letter-spacing:2px;cursor:pointer;font-family:monospace;}
button:hover{background:rgba(126,184,218,0.15);}
.err{color:#c44;margin-top:12px;font-size:13px;}
</style></head><body>
<div class="box">
<h1>ATLAS</h1>
<div class="sub">pocket veritasium / kurzgesagt</div>
<form method="POST" action="/login">
<input type="password" name="password" placeholder="Password" autofocus>
<button type="submit">ENTER</button>
</form>
{{error}}
</div></body></html>"""


APP_HTML = r"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Atlas</title>
<style>
:root{
  --bg:#0a0a0f;--bg2:#0f0f1a;--fg:#e0ddd5;--dim:#6a6860;
  --accent:#7eb8da;--accent2:#5a9aba;--green:#6abf8a;--yellow:#d4b96a;
  --magenta:#b87adb;--surface:rgba(15,15,25,0.85);
}
*{box-sizing:border-box;margin:0;padding:0;}
html{font-size:16px;}
body{background:var(--bg);color:var(--fg);font-family:Georgia,'Times New Roman',serif;
  line-height:1.7;min-height:100vh;-webkit-font-smoothing:antialiased;}

/* Header */
.header{position:sticky;top:0;z-index:100;background:rgba(10,10,15,0.95);
  backdrop-filter:blur(12px);padding:16px 20px;border-bottom:1px solid rgba(126,184,218,0.08);}
.header h1{font-size:18px;font-weight:400;letter-spacing:6px;color:var(--accent);display:inline;}
.header .sub{font-size:8px;letter-spacing:3px;color:rgba(126,184,218,0.3);margin-left:8px;text-transform:uppercase;}
.nav{display:flex;gap:4px;margin-top:12px;}
.nav button{flex:1;padding:10px 6px;background:var(--surface);border:1px solid rgba(126,184,218,0.1);
  border-radius:6px;color:var(--dim);font-size:11px;font-family:monospace;letter-spacing:1px;cursor:pointer;transition:all 0.2s;}
.nav button:hover,.nav button.active{color:var(--accent);border-color:rgba(126,184,218,0.3);background:rgba(126,184,218,0.06);}

/* Views */
.view{display:none;padding:20px;padding-bottom:120px;max-width:720px;margin:0 auto;}
.view.active{display:block;}

/* Mode selector */
.modes{display:flex;gap:8px;margin-bottom:20px;}
.mode-btn{flex:1;padding:14px 8px;background:var(--surface);border:1px solid rgba(255,255,255,0.06);
  border-radius:8px;text-align:center;cursor:pointer;transition:all 0.2s;}
.mode-btn:hover{border-color:rgba(126,184,218,0.2);}
.mode-btn.active{border-color:var(--accent);background:rgba(126,184,218,0.08);}
.mode-btn .icon{font-size:22px;margin-bottom:4px;}
.mode-btn .label{font-size:11px;font-family:monospace;letter-spacing:1px;color:var(--dim);}
.mode-btn.active .label{color:var(--accent);}

/* Input area */
.input-area{margin-bottom:20px;}
.input-area textarea{width:100%;padding:14px 16px;background:var(--surface);border:1px solid rgba(126,184,218,0.12);
  border-radius:8px;color:var(--fg);font-size:15px;font-family:Georgia,serif;resize:none;outline:none;
  line-height:1.5;min-height:52px;max-height:150px;}
.input-area textarea:focus{border-color:rgba(126,184,218,0.35);}
.input-area textarea::placeholder{color:var(--dim);}
.angle-input{margin-top:8px;}
.go-btn{width:100%;padding:14px;margin-top:12px;background:rgba(126,184,218,0.1);
  border:1px solid rgba(126,184,218,0.3);border-radius:8px;color:var(--accent);
  font-size:13px;font-family:monospace;letter-spacing:2px;cursor:pointer;transition:all 0.2s;}
.go-btn:hover{background:rgba(126,184,218,0.18);}
.go-btn:disabled{opacity:0.4;cursor:not-allowed;}

/* Exploration display */
.exploration{animation:fadeIn 0.4s ease;}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:none;}}
.exp-header{margin-bottom:24px;padding:20px;background:var(--surface);border-radius:10px;
  border-left:3px solid var(--accent);}
.exp-mode{font-size:10px;font-family:monospace;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;}
.exp-mode.surprise{color:var(--green);}
.exp-mode.thread{color:var(--accent);}
.exp-mode.deep{color:var(--magenta);}
.exp-title{font-size:22px;font-weight:400;line-height:1.3;}

.narrative{font-size:16px;line-height:1.8;}
.narrative p{margin-bottom:1.2em;}
.narrative h1,.narrative h2,.narrative h3{color:var(--accent);font-weight:400;margin:1.5em 0 0.6em;}
.narrative h2{font-size:20px;}
.narrative h3{font-size:17px;}
.narrative blockquote{border-left:2px solid var(--accent);padding-left:16px;color:var(--dim);font-style:italic;margin:1em 0;}
.narrative strong{color:#fff;font-weight:600;}
.narrative em{color:var(--yellow);}
.narrative a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(126,184,218,0.3);}
.narrative a:hover{border-color:var(--accent);}
.narrative code{background:rgba(126,184,218,0.08);padding:2px 6px;border-radius:3px;font-size:14px;}

/* Thread card */
.thread-card{margin-top:28px;padding:18px 20px;background:rgba(212,185,106,0.05);
  border:1px solid rgba(212,185,106,0.2);border-radius:10px;cursor:pointer;transition:all 0.2s;}
.thread-card:hover{background:rgba(212,185,106,0.1);border-color:rgba(212,185,106,0.4);}
.thread-label{font-size:10px;font-family:monospace;letter-spacing:2px;color:var(--yellow);margin-bottom:6px;}
.thread-text{font-style:italic;color:var(--yellow);font-size:15px;line-height:1.5;}

/* Tags */
.tags{margin-top:20px;display:flex;flex-wrap:wrap;gap:6px;}
.tag{font-size:11px;font-family:monospace;color:var(--dim);background:rgba(255,255,255,0.03);
  padding:4px 10px;border-radius:12px;border:1px solid rgba(255,255,255,0.06);}

/* History */
.history-item{padding:16px;background:var(--surface);border-radius:8px;margin-bottom:8px;
  cursor:pointer;transition:all 0.15s;border:1px solid transparent;}
.history-item:hover{border-color:rgba(126,184,218,0.15);}
.history-title{font-size:15px;margin-bottom:4px;}
.history-meta{font-size:11px;font-family:monospace;color:var(--dim);}
.history-thread{font-size:12px;color:var(--yellow);font-style:italic;margin-top:6px;}

/* Status */
.status{text-align:center;padding:40px 20px;color:var(--dim);font-style:italic;}
.status .spinner{display:inline-block;width:18px;height:18px;border:2px solid rgba(126,184,218,0.2);
  border-top-color:var(--accent);border-radius:50%;animation:spin 0.8s linear infinite;margin-right:10px;vertical-align:middle;}
@keyframes spin{to{transform:rotate(360deg);}}

/* Scrollbar */
::-webkit-scrollbar{width:6px;}
::-webkit-scrollbar-track{background:transparent;}
::-webkit-scrollbar-thumb{background:rgba(126,184,218,0.15);border-radius:3px;}

/* Mobile tweaks */
@media(max-width:480px){
  .header{padding:12px 16px;}
  .header h1{font-size:16px;letter-spacing:5px;}
  .view{padding:16px;padding-bottom:100px;}
  .exp-title{font-size:19px;}
  .narrative{font-size:15px;}
  .modes{flex-wrap:wrap;}
  .mode-btn{min-width:calc(50% - 4px);}
}
</style>
</head>
<body>

<div class="header">
  <h1>ATLAS</h1><span class="sub">pocket veritasium / kurzgesagt</span>
  <div class="nav">
    <button class="active" onclick="showView('explore')">EXPLORE</button>
    <button onclick="showView('history')">HISTORY</button>
  </div>
</div>

<!-- EXPLORE VIEW -->
<div id="view-explore" class="view active">
  <div id="explore-input">
    <div class="modes">
      <div class="mode-btn active" data-mode="surprise" onclick="setMode('surprise')">
        <div class="icon">?</div>
        <div class="label">SURPRISE</div>
      </div>
      <div class="mode-btn" data-mode="thread" onclick="setMode('thread')">
        <div class="icon">&rarr;</div>
        <div class="label">THREAD</div>
      </div>
      <div class="mode-btn" data-mode="deep" onclick="setMode('deep')">
        <div class="icon">&darr;</div>
        <div class="label">DEEP DIVE</div>
      </div>
    </div>

    <div class="input-area" id="input-thread" style="display:none;">
      <textarea id="thread-input" placeholder="What are you curious about?" rows="2"></textarea>
    </div>
    <div class="input-area" id="input-deep" style="display:none;">
      <textarea id="deep-input" placeholder="Topic?" rows="2"></textarea>
      <textarea id="deep-angle" class="angle-input" placeholder="Angle (optional)" rows="1"></textarea>
    </div>

    <button class="go-btn" id="go-btn" onclick="startExploration()">EXPLORE</button>
  </div>

  <div id="explore-output"></div>
</div>

<!-- HISTORY VIEW -->
<div id="view-history" class="view">
  <div id="history-list"></div>
</div>

<script>
let currentMode = 'surprise';
let exploring = false;

function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + name).classList.add('active');
  document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  if (name === 'history') loadHistory();
}

function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
  document.getElementById('input-thread').style.display = mode === 'thread' ? 'block' : 'none';
  document.getElementById('input-deep').style.display = mode === 'deep' ? 'block' : 'none';
}

function startExploration() {
  if (exploring) return;
  exploring = true;

  const btn = document.getElementById('go-btn');
  btn.disabled = true;
  btn.textContent = 'EXPLORING...';

  const body = { mode: currentMode };
  if (currentMode === 'thread') {
    body.input = document.getElementById('thread-input').value;
    if (!body.input.trim()) { reset(); return; }
  } else if (currentMode === 'deep') {
    body.input = document.getElementById('deep-input').value;
    body.angle = document.getElementById('deep-angle').value;
    if (!body.input.trim()) { reset(); return; }
  }

  const output = document.getElementById('explore-output');
  output.innerHTML = '<div class="status"><span class="spinner"></span>Falling down the rabbit hole...</div>';

  streamExplore('/api/explore', body, output);
}

function followThread(thread) {
  if (exploring) return;
  exploring = true;

  const output = document.getElementById('explore-output');
  output.innerHTML = '<div class="status"><span class="spinner"></span>Following the thread...</div>';

  showViewDirect('explore');
  streamExplore('/api/next', { thread: thread }, output);
}

function showViewDirect(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + name).classList.add('active');
  document.querySelectorAll('.nav button').forEach((b, i) => {
    b.classList.toggle('active', (name === 'explore' && i === 0) || (name === 'history' && i === 1));
  });
}

function streamExplore(url, body, output) {
  const phases = [
    [0, 'Launching exploration...'],
    [5000, 'Falling down the rabbit hole...'],
    [15000, 'Searching for what most people miss...'],
    [30000, 'Digging through primary sources...'],
    [60000, 'Cross-referencing the evidence...'],
    [90000, 'Crafting the narrative...'],
    [120000, 'Going deep -- this is a thorough one...'],
    [180000, 'Still going... must have found something fascinating'],
  ];
  let phaseIdx = 0;
  const startTime = Date.now();
  const phaseTimer = setInterval(() => {
    const elapsed = Date.now() - startTime;
    while (phaseIdx < phases.length - 1 && elapsed > phases[phaseIdx + 1][0]) phaseIdx++;
    const statusEl = output.querySelector('.status');
    if (statusEl) statusEl.innerHTML = '<span class="spinner"></span>' + phases[phaseIdx][1];
  }, 1000);

  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(response => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    function read() {
      reader.read().then(({ done, value }) => {
        if (done) { clearInterval(phaseTimer); reset(); return; }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));

            if (data.type === 'status') {
              const statusEl = output.querySelector('.status');
              if (statusEl) statusEl.innerHTML = '<span class="spinner"></span>' + data.message;
            }

            if (data.type === 'text') {
              clearInterval(phaseTimer);
              output.innerHTML = '<div class="exploration"><div class="narrative"></div></div>';
              renderMarkdown(output.querySelector('.narrative'), data.content);
            }

            if (data.type === 'meta') {
              renderFinal(output.querySelector('.exploration') || output, data.meta, data.narrative, data.id);
            }

            if (data.type === 'error') {
              clearInterval(phaseTimer);
              output.innerHTML = '<div class="status" style="color:#c44;">' + escHtml(data.message) + '</div>';
              reset();
            }

            if (data.type === 'done') {
              clearInterval(phaseTimer);
              reset();
            }
          } catch(e) {}
        }
        read();
      });
    }
    read();
  }).catch(err => {
    clearInterval(phaseTimer);
    output.innerHTML = '<div class="status" style="color:#c44;">' + escHtml(err.message) + '</div>';
    reset();
  });
}

function renderMarkdown(el, text) {
  let html = text
    .replace(/```atlas-meta[\s\S]*?```/g, '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
    .replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>');
  el.innerHTML = '<p>' + html + '</p>';
}

function renderFinal(container, meta, narrative, id) {
  const narEl = container.querySelector('.narrative');
  if (narEl) renderMarkdown(narEl, narrative);

  // Add header
  const header = document.createElement('div');
  header.className = 'exp-header';
  header.innerHTML = `
    <div class="exp-mode ${currentMode}">${currentMode.toUpperCase()}</div>
    <div class="exp-title">${escHtml(meta.title)}</div>
  `;
  container.insertBefore(header, container.firstChild);

  // Thread card
  if (meta.next_thread) {
    const card = document.createElement('div');
    card.className = 'thread-card';
    card.onclick = () => followThread(meta.next_thread);
    card.innerHTML = `
      <div class="thread-label">NEXT THREAD</div>
      <div class="thread-text">${escHtml(meta.next_thread)}</div>
    `;
    container.appendChild(card);
  }

  // Tags
  if (meta.tags && meta.tags.length) {
    const tagsDiv = document.createElement('div');
    tagsDiv.className = 'tags';
    meta.tags.forEach(t => {
      tagsDiv.innerHTML += `<span class="tag">#${escHtml(t)}</span>`;
    });
    container.appendChild(tagsDiv);
  }
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function reset() {
  exploring = false;
  const btn = document.getElementById('go-btn');
  btn.disabled = false;
  btn.textContent = 'EXPLORE';
}

async function loadHistory() {
  const list = document.getElementById('history-list');
  list.innerHTML = '<div class="status">Loading...</div>';
  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    if (!data.length) {
      list.innerHTML = '<div class="status">No explorations yet. Go explore!</div>';
      return;
    }
    list.innerHTML = '';
    data.forEach(e => {
      const modeColors = { surprise: 'var(--green)', thread: 'var(--accent)', deep: 'var(--magenta)' };
      const div = document.createElement('div');
      div.className = 'history-item';
      div.onclick = () => revisit(e.id);
      div.innerHTML = `
        <div class="history-title">${escHtml(e.title)}</div>
        <div class="history-meta">
          <span style="color:${modeColors[e.mode] || 'var(--dim)'}">${(e.mode||'').toUpperCase()}</span>
          &middot; ${(e.timestamp||'').slice(0,10)}
          &middot; ${(e.tags||[]).map(t => '#'+t).join(' ')}
        </div>
        ${e.next_thread ? '<div class="history-thread">' + escHtml(e.next_thread) + '</div>' : ''}
      `;
      list.appendChild(div);
    });
  } catch(err) {
    list.innerHTML = '<div class="status" style="color:#c44;">Failed to load history</div>';
  }
}

async function revisit(id) {
  showViewDirect('explore');
  const output = document.getElementById('explore-output');
  output.innerHTML = '<div class="status"><span class="spinner"></span>Loading...</div>';

  try {
    const res = await fetch('/api/revisit/' + id);
    const data = await res.json();
    if (!data.ok) { output.innerHTML = '<div class="status">Not found</div>'; return; }

    currentMode = data.mode || 'surprise';
    const container = document.createElement('div');
    container.className = 'exploration';
    container.innerHTML = '<div class="narrative"></div>';
    output.innerHTML = '';
    output.appendChild(container);

    const meta = { title: data.title, tags: data.tags || [], next_thread: data.next_thread || '' };
    renderMarkdown(container.querySelector('.narrative'), data.narrative || '');
    renderFinal(container, meta, data.narrative || '', id);
  } catch(err) {
    output.innerHTML = '<div class="status" style="color:#c44;">' + escHtml(err.message) + '</div>';
  }
}

// Auto-resize textareas
document.querySelectorAll('textarea').forEach(ta => {
  ta.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 150) + 'px';
  });
  ta.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      startExploration();
    }
  });
});
</script>
</body></html>"""


# -- Startup ------------------------------------------------------------------
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    ip = get_local_ip()
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  ATLAS Web")
    print(f"  Local:   http://localhost:{port}")
    print(f"  Phone:   http://{ip}:{port}")
    print(f"  Password: {APP_PASSWORD}")
    print(f"  (Set ATLAS_PASSWORD env var to change)\n")
    app.run(host="0.0.0.0", port=port, debug=False)
