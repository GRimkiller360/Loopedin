"""Content-quality checks for a pending script, run right after schema validation and
before any production cost (TTS/b-roll/assembly/upload) is spent on it.

Unlike script_schema.py (structural shape, self-contained), these checks are heuristic
and need history (state/used_topics.json) to catch things like accidental near-duplicate
titles or lazy filler openers slipping past the routine's own instructions. A failure
here is treated the same as any other production failure by produce-upload.yml --
counts toward auto-pause, leaves state/pending_script.json in place for a human to
inspect (see ROUTINE_INSTRUCTIONS.md step 0.5).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.state_utils import load_json

MIN_WORDS, MAX_WORDS = 25, 160
RECENT_TITLES_TO_CHECK = 15
TITLE_OVERLAP_THRESHOLD = 0.7

BANNED_OPENERS = (
    "so today", "in this video", "welcome back", "today we're talking about",
    "today we are talking about", "let's talk about", "in today's video",
    "hey guys", "what's up everyone",
)


def _word_set(text):
    return {w.strip(".,!?:;\"'").lower() for w in text.split() if w.strip(".,!?:;\"'")}


def _title_overlap(a, b):
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def check(script, used_topics_path):
    errors = []

    beats = script.get("beats") or []
    total_words = sum(len(b.get("text", "").split()) for b in beats)
    if not (MIN_WORDS <= total_words <= MAX_WORDS):
        errors.append(f"narration word count {total_words} outside expected {MIN_WORDS}-{MAX_WORDS} range")

    if beats:
        opener = beats[0].get("text", "").strip().lower()
        for banned in BANNED_OPENERS:
            if opener.startswith(banned):
                errors.append(f"beat 0 opens with a banned filler phrase: {banned!r}")
                break

    used = load_json(used_topics_path, {"topics": []})
    recent_titles = [t["title"] for t in used["topics"][-RECENT_TITLES_TO_CHECK:] if t.get("title")]
    title = script.get("title", "")
    for recent in recent_titles:
        overlap = _title_overlap(title, recent)
        if overlap >= TITLE_OVERLAP_THRESHOLD:
            errors.append(f"title too similar (overlap={overlap:.2f}) to a recent title: {recent!r}")
            break

    return errors


if __name__ == "__main__":
    script_path, used_topics_path = sys.argv[1], sys.argv[2]
    data = json.loads(open(script_path, encoding="utf-8").read())
    errors = check(data, used_topics_path)
    if errors:
        print("QUALITY GATE FAILED:\n" + "\n".join(errors))
        sys.exit(1)
    print("ok")
