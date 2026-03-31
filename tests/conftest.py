"""Shared fixtures for the Atlas test suite."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _make_exploration(
    *,
    id=None,
    mode="surprise",
    title="Test Exploration",
    narrative="A short test narrative.",
    tags=None,
    sources=None,
    next_thread="What happens next?",
    connections=None,
    input_text=None,
    timestamp=None,
):
    return {
        "id": id or uuid.uuid4().hex[:8],
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "input_text": input_text,
        "title": title,
        "narrative": narrative,
        "tags": tags if tags is not None else ["science", "history"],
        "sources": sources if sources is not None else [],
        "next_thread": next_thread,
        "connections": connections if connections is not None else [],
    }


@pytest.fixture
def make_exploration():
    return _make_exploration


@pytest.fixture
def sample_exploration():
    return _make_exploration(
        id="abc12345",
        title="The Hidden Life of Slime Molds",
        narrative=(
            "Most people think intelligence requires a brain. "
            "They are wrong.

"
            "## The Blob That Solved the Rail Map

"
            "In 2010, researchers at Hokkaido University placed oat "
            "flakes on a map of Tokyo rail stations."
        ),
        tags=["biology", "intelligence", "networks"],
        sources=[{"url": "https://example.com/slime", "title": "Slime Paper"}],
        next_thread="Can slime molds predict earthquakes?",
        mode="surprise",
    )


@pytest.fixture
def sample_history(sample_exploration, make_exploration):
    return [
        sample_exploration,
        make_exploration(
            id="def67890", mode="thread",
            title="Why Hospitals Smell Like That",
            tags=["chemistry", "medicine"],
            next_thread="The antibiotic apocalypse timeline",
            input_text="hospital smell",
        ),
        make_exploration(
            id="ghi11111", mode="deep",
            title="CRISPR Off-Target Problem",
            tags=["biology", "genetics", "medicine"],
            next_thread="Gene drives in wild mosquitoes",
            input_text="CRISPR",
        ),
    ]


@pytest.fixture
def history_file(tmp_path, sample_history):
    hfile = tmp_path / ".atlas" / "history.json"
    hfile.parent.mkdir(parents=True, exist_ok=True)
    hfile.write_text(json.dumps(sample_history, indent=2), encoding="utf-8")
    return hfile


@pytest.fixture
def exploration_dataclass(sample_exploration):
    from atlas.engine import Exploration
    return Exploration(**sample_exploration)


@pytest.fixture
def patched_history(tmp_path, monkeypatch):
    atlas_dir = tmp_path / ".atlas"
    atlas_dir.mkdir(exist_ok=True)
    history_file = atlas_dir / "history.json"
    import atlas.history as hist_mod
    monkeypatch.setattr(hist_mod, "ATLAS_DIR", atlas_dir)
    monkeypatch.setattr(hist_mod, "HISTORY_FILE", history_file)
    return {"dir": atlas_dir, "file": history_file}


@pytest.fixture
def fake_claude_result():
    def _make(
        narrative="Test output.",
        title="Test Title",
        tags=None,
        next_thread="Next?",
        returncode=0,
        is_error=False,
    ):
        if tags is None:
            tags = ["test"]
        meta = json.dumps({
            "title": title, "tags": tags,
            "next_thread": next_thread, "connections": [],
        })
        raw = f"{narrative}

" + ""
        result_json = json.dumps({"result": raw, "is_error": is_error})

        class FakeResult:
            pass
        r = FakeResult()
        r.returncode = returncode
        r.stdout = result_json
        r.stderr = ""
        return r
    return _make
