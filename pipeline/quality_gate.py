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
MIN_HOOK_WINNER_OVERLAP = 0.15
HOOK_CANDIDATE_OVERLAP_THRESHOLD = 0.75

BANNED_OPENERS = (
    "so today", "in this video", "welcome back", "today we're talking about",
    "today we are talking about", "let's talk about", "in today's video",
    "hey guys", "what's up everyone",
    # Evidence-backed, not a guess: this channel's two weakest-retention hooks
    # (29.7% and 53.6% avg view) both opened with this exact pattern -- a familiar
    # observation the viewer already agrees with, not a claim that creates tension.
    # None of the strong hooks (up to 97.8%) use it. See ROUTINE_INSTRUCTIONS.md
    # step 2.1 for the full "pattern-observation vs specific claim" reasoning.
    "every year", "every few months", "every time",
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

    # The winning hook_candidate must actually be the one used -- catches "planning
    # theater" where candidates get drafted per the schema but beat 0 is written as
    # something unrelated anyway. Light-touch polish between candidate and final beat 0
    # is fine (hence a low bar, not an exact match), but zero resemblance means the
    # planning step was skipped in substance even though the file satisfies the schema.
    winner = next(
        (c for c in (script.get("hook_candidates") or []) if c.get("hook_type") == script.get("hook_type")),
        None,
    )
    if winner and beats:
        overlap = _title_overlap(winner.get("text", ""), beats[0].get("text", ""))
        if overlap < MIN_HOOK_WINNER_OVERLAP:
            errors.append(
                f"beat 0 doesn't resemble the winning hook_candidate (overlap={overlap:.2f}) -- "
                "the drafted hook that matched hook_type must actually be the one used, not ignored"
            )

    # Candidates need to be genuinely different options, not the same idea reworded --
    # schema.py only catches exact-duplicate text; this catches near-duplicates.
    candidates = script.get("hook_candidates") or []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            overlap = _title_overlap(candidates[i].get("text", ""), candidates[j].get("text", ""))
            if overlap >= HOOK_CANDIDATE_OVERLAP_THRESHOLD:
                errors.append(
                    f"hook_candidates[{i}] and [{j}] are too similar (overlap={overlap:.2f}) -- "
                    "these need to be genuinely distinct angles, not the same hook reworded"
                )

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
